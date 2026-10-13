"""RESOURCE_BINDING_GATE / AUTHORIZATION_RESOLUTION_GATE.

The file is opened FIRST (during authorization). Identity and final path come
from the open handle, Policy judges the open object, and EXEC reads from that
same handle through a runtime binding table. So:

  * a swap DURING authorization (between "decide the path" and "open it", or
    right after the open) cannot make Policy authorize one object while another
    is read;
  * a swap AFTER authorization cannot change what EXEC reads: it reads the
    object that was authorized, never the path.

Scope: Windows/NTFS/fs.read. Not covered (deferred): byte changes of the same
file, ReFS, ext4, cross-adapter replay, durable evidence.
"""
import dataclasses
import os
import subprocess
import sys

import pytest

from governance.aep_adapter import AdapterError
from governance.aep_executors import ExecutorError, default_registry
from governance.aep_runtime import AEPRuntime
from governance.contract import AuthorizedAction, Decision
from governance.handle_binding import BindingError, BindingTable, open_native


# ---- helpers ---------------------------------------------------------------------

class Swapper:
    """Opener seam: runs attacker code right before and/or right after the real open."""

    def __init__(self):
        self.before = None
        self.after = None

    def __call__(self, path):
        before, after = self.before, self.after
        self.before = self.after = None  # one-shot
        if before:
            before()
        fd = open_native(path)
        if after:
            after()
        return fd


def _symlink(src, dst, directory=False):
    try:
        os.symlink(src, dst, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable here")


def _rm_link(path):
    try:
        os.remove(path)
    except OSError:
        os.rmdir(path)


def _authorize(env, raw="a.txt"):
    out = env.authorizer.authorize(env.read(raw))
    assert out.final_decision.decision is Decision.ALLOW, out
    action = out.authorized_action
    assert action.resource_identity and action.resource_binding_id  # bound at authorization
    return action


def _bytes_read_from(env, path):
    return sum(n for p, _, n in env.bindings.read_log() if os.path.normcase(p) == os.path.normcase(path))


def _assert_denied_without_reading_anything(env, out):
    assert out.final_decision.decision is Decision.DENY
    assert out.authorized_action is None
    assert env.bindings.pending() == 0  # the open handle was released
    assert env.bindings.read_log() == ()  # 0 bytes read from anywhere
    assert env.adapter.effect_log() == [] and env.spy.calls == []
    assert env.oracle_violations() == []
    assert "secret" not in repr(out)


def _assert_original_read(env, action, result, expected, outside_paths):
    assert result.ok and result.output == expected
    assert result.resource_identity == action.resource_identity  # executed == authorized
    assert "secret" not in repr(result)
    for p in outside_paths:
        assert _bytes_read_from(env, p) == 0  # outside\secret.txt: 0 bytes read
    assert all(os.path.normcase(os.path.dirname(p)).startswith(os.path.normcase(env.root))
               for p, _, _ in env.bindings.read_log())
    assert env.bindings.pending() == 0
    assert env.oracle_violations() == []


# ---- AUTHORIZATION_RESOLUTION_GATE: swap DURING authorization ----------------------

def test_auth_flipflop_symlink_to_outside_during_open_is_denied_and_reads_nothing(make_env):
    """Classic TOCTOU: the path points outside only while it is being opened, and is
    restored before anything re-checks it. A path-string validation would say
    'inside'; the handle is the outside file. Policy must judge the handle."""
    sw = Swapper()
    env = make_env(opener=sw)
    outside = os.path.join(env.base, "outside.txt")  # "secret"
    orig = env.path("a.orig")

    def before():
        os.rename(env.path("a.txt"), orig)
        _symlink(outside, env.path("a.txt"))

    def after():
        _rm_link(env.path("a.txt"))
        os.rename(orig, env.path("a.txt"))

    sw.before, sw.after = before, after
    out = env.authorizer.authorize(env.read("a.txt"))
    _assert_denied_without_reading_anything(env, out)
    assert os.path.normcase(out.resource) == os.path.normcase(outside)  # judged: the open object
    assert _bytes_read_from(env, outside) == 0


def test_auth_parent_dir_symlink_to_outside_during_open_is_denied(make_env):
    sw = Swapper()
    env = make_env(opener=sw)
    with open(env.path("sub/f.txt"), "w") as fh:
        fh.write("inside")
    elsewhere = os.path.join(env.base, "elsewhere")
    os.makedirs(elsewhere)
    with open(os.path.join(elsewhere, "f.txt"), "w") as fh:
        fh.write("secret-dir")

    def before():
        os.rename(env.path("sub"), env.path("sub_old"))
        _symlink(elsewhere, env.path("sub"), directory=True)

    def after():
        _rm_link(env.path("sub"))
        os.rename(env.path("sub_old"), env.path("sub"))

    sw.before, sw.after = before, after
    out = env.authorizer.authorize(env.read("sub/f.txt"))
    _assert_denied_without_reading_anything(env, out)
    assert _bytes_read_from(env, os.path.join(elsewhere, "f.txt")) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_auth_parent_dir_junction_to_outside_during_open_is_denied(make_env):
    sw = Swapper()
    env = make_env(opener=sw)
    with open(env.path("sub/f.txt"), "w") as fh:
        fh.write("inside")
    elsewhere = os.path.join(env.base, "elsewhere")
    os.makedirs(elsewhere)
    with open(os.path.join(elsewhere, "f.txt"), "w") as fh:
        fh.write("secret-junction")

    def before():
        os.rename(env.path("sub"), env.path("sub_old"))
        made = subprocess.run(["cmd", "/c", "mklink", "/J", env.path("sub"), elsewhere],
                              capture_output=True, text=True)
        if made.returncode != 0:
            os.rename(env.path("sub_old"), env.path("sub"))
            pytest.skip("junction could not be created: %s" % made.stdout.strip())

    def after():
        os.rmdir(env.path("sub"))  # removes the junction only
        os.rename(env.path("sub_old"), env.path("sub"))

    sw.before, sw.after = before, after
    out = env.authorizer.authorize(env.read("sub/f.txt"))
    _assert_denied_without_reading_anything(env, out)
    assert _bytes_read_from(env, os.path.join(elsewhere, "f.txt")) == 0


def test_auth_swap_right_after_open_still_reads_the_object_that_was_opened(make_env):
    """Namespace swapped to point outside immediately AFTER the open, still during
    authorization. The authorized object is the open one; the secret is never read."""
    sw = Swapper()
    env = make_env(opener=sw)
    outside = os.path.join(env.base, "outside.txt")

    def after():
        os.rename(env.path("a.txt"), env.path("a.orig"))
        _symlink(outside, env.path("a.txt"))

    sw.after = after
    out = env.authorizer.authorize(env.read("a.txt"))
    assert out.final_decision.decision is Decision.ALLOW
    action = out.authorized_action
    result = env.adapter.execute(action, env.store)
    _assert_original_read(env, action, result, "hello", [outside])


def test_auth_binding_is_opened_first_and_pending_until_exec(env):
    assert env.bindings.pending() == 0
    action = _authorize(env)
    assert env.bindings.pending() == 1  # one open handle, held by the runtime table
    env.adapter.execute(action, env.store)
    assert env.bindings.pending() == 0


def test_auth_non_allow_outcomes_release_the_handle(env):
    for raw in (os.path.join(env.base, "outside.txt"), "../ws_evil/secret.txt"):
        out = env.run(env.read(raw))
        assert out.final_decision.decision is Decision.DENY
    out = env.run(env.read("a.txt", source="tool_output"))  # provenance denial after a real open
    assert out.final_decision.decision is Decision.DENY
    assert env.bindings.pending() == 0 and env.bindings.read_log() == ()
    assert env.spy.calls == []


# ---- post-authorization: swap AFTER authorization -----------------------------------

def test_binding_post_auth_swap_file_to_symlink_reads_original_not_secret(env):
    action = _authorize(env)
    outside = os.path.join(env.base, "outside.txt")  # "secret"
    os.rename(env.path("a.txt"), env.path("a.orig"))
    _symlink(outside, env.path("a.txt"))
    result = env.adapter.execute(action, env.store)
    _assert_original_read(env, action, result, "hello", [outside])


def test_binding_post_auth_swap_for_a_different_regular_file_reads_original(env):
    action = _authorize(env)
    with open(env.path("impostor.tmp"), "w") as fh:
        fh.write("impostor")
    os.rename(env.path("a.txt"), env.path("a.orig"))
    os.replace(env.path("impostor.tmp"), env.path("a.txt"))
    result = env.adapter.execute(action, env.store)
    _assert_original_read(env, action, result, "hello", [])
    assert "impostor" not in repr(result)


def test_binding_post_auth_parent_dir_swap_cannot_change_what_is_read(env):
    with open(env.path("sub/f.txt"), "w") as fh:
        fh.write("inside")
    action = _authorize(env, "sub/f.txt")
    elsewhere = os.path.join(env.base, "elsewhere")
    os.makedirs(elsewhere)
    with open(os.path.join(elsewhere, "f.txt"), "w") as fh:
        fh.write("secret-dir")
    try:
        os.rename(env.path("sub"), env.path("sub_old"))
        blocked = False
    except OSError:
        blocked = True  # Windows refuses to rename a directory that holds an open file
    if not blocked:
        _symlink(elsewhere, env.path("sub"), directory=True)
    result = env.adapter.execute(action, env.store)
    _assert_original_read(env, action, result, "inside", [os.path.join(elsewhere, "f.txt")])


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_binding_post_auth_parent_dir_junction_cannot_change_what_is_read(env):
    with open(env.path("sub/f.txt"), "w") as fh:
        fh.write("inside")
    action = _authorize(env, "sub/f.txt")
    elsewhere = os.path.join(env.base, "elsewhere")
    os.makedirs(elsewhere)
    with open(os.path.join(elsewhere, "f.txt"), "w") as fh:
        fh.write("secret-junction")
    try:
        os.rename(env.path("sub"), env.path("sub_old"))
    except OSError:
        pass  # blocked by the OS: the open handle pins the directory
    else:
        made = subprocess.run(["cmd", "/c", "mklink", "/J", env.path("sub"), elsewhere],
                              capture_output=True, text=True)
        if made.returncode != 0:
            pytest.skip("junction could not be created: %s" % made.stdout.strip())
    result = env.adapter.execute(action, env.store)
    _assert_original_read(env, action, result, "inside", [os.path.join(elsewhere, "f.txt")])


def test_binding_positive_unchanged_file_is_read_through_the_same_identity(env):
    action = _authorize(env)
    result = env.adapter.execute(action, env.store)
    assert result.ok and result.output == "hello"
    assert result.resource_identity == action.resource_identity
    assert env.adapter.effect_log()[0]["resource_identity"] == action.resource_identity
    assert env.oracle_violations() == []


def test_binding_documented_semantics_identity_binds_the_file_not_its_bytes(env):
    action = _authorize(env)
    with open(env.path("a.txt"), "w") as fh:  # same file, new content
        fh.write("changed")
    result = env.adapter.execute(action, env.store)
    assert result.ok and result.output == "changed"  # NOT covered by this gate


def test_binding_authorization_requires_an_existing_regular_file(env):
    for raw in ("missing.txt", "sub"):  # nonexistent, directory
        out = env.run(env.read(raw))
        assert out.final_decision.decision is Decision.DENY
        assert out.authorized_action is None
    assert env.spy.calls == [] and env.bindings.pending() == 0


# ---- binding is part of the recorded authority; single use --------------------------

def test_binding_identity_and_id_are_part_of_the_recorded_authority(env):
    action = _authorize(env)
    auth = [e for e in env.store.events() if e["kind"] == "AUTHORIZATION"][0]
    assert auth["resource_identity"] == action.resource_identity
    assert auth["resource_binding_id"] == action.resource_binding_id
    for forged in (dataclasses.replace(action, resource_identity="1:1"),
                   dataclasses.replace(action, resource_binding_id="b-forged")):
        with pytest.raises(AdapterError, match="authorization_mismatch"):
            env.adapter.execute(forged, env.store)
    assert env.spy.calls == [] and env.adapter.effect_log() == []
    assert env.bindings.pending() == 1  # the real binding is untouched until its own EXEC


def test_binding_is_single_use(env):
    action = _authorize(env)
    env.adapter.execute(action, env.store)
    with pytest.raises(BindingError, match="binding_unavailable"):
        env.bindings.take(action.resource_binding_id)


def test_binding_executor_refuses_a_binding_whose_identity_is_not_the_authorized_one(env):
    """Defense in depth below the adapter: the AEP is handed an authorization that
    names file B's identity but a binding that holds file A."""
    from aep_support import Standalone, envelope_for, record_for

    with open(env.path("b.txt"), "w") as fh:
        fh.write("bbb")
    table = BindingTable()
    bid_a, path_a, _ = table.open(env.path("a.txt"))
    _, _, ident_b = table.open(env.path("b.txt"))
    sa = Standalone(table)
    rec = record_for("ev-0", "i", "fs.read", "FS_READ", (), bid_a, path_a, ident_b)
    sa.issuer.issue(rec)
    with pytest.raises(ExecutorError, match="resource_identity_mismatch"):
        sa.runtime.submit(envelope_for(rec))
    assert table.read_log() == () and sa.registry.effects() == ()
    assert sa.ledger.consumed_by("ev-0") is not None  # failed after consume: stays consumed
