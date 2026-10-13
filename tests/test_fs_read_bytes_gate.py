"""IAOS_FS_READ_BYTES_GATE: ``fs.read_bytes`` is the SAME read authority as ``fs.read`` (same open-first binding, same
policy/root, same authorization flow, same single-use consumption) returning the raw bytes intact.

Governance does not decode, encode, base64 or parse the content; ``result_digest`` is SHA-256 over the raw bytes.
Out of scope here: documents, PDF/image handling, Agnes, UI.
Scope note: certified on Windows/NTFS like fs.read.
"""
import hashlib
import os

import pytest

from governance.aep_adapter import AdapterError
from governance.aep_executors import ExecutorError, default_registry
from governance.contract import ACTION_REGISTRY, ActionSpec, Decision, RiskLevel
from governance.handle_binding import BindingTable

from aep_support import Standalone, envelope_for, record_for

RAW = bytes(range(256)) * 3 + b"\xff\xfe\x00\x01 not utf-8 \x80\x81"
BIG = (bytes(range(256)) * 1200)[:300_001]  # > the 64 KiB read chunk, odd length


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def _bytes_proposal(env, raw, **kw):
    return env.proposal("fs.read_bytes", {"path": raw}, **kw)


def _authorize_bytes(env, raw="bin.dat"):
    out = env.authorizer.authorize(_bytes_proposal(env, raw))
    assert out.final_decision.decision is Decision.ALLOW, out.policy_decision
    return out.authorized_action


def snapshot(base):
    return sorted((str(p.relative_to(base)), p.stat().st_size, p.stat().st_mtime_ns) for p in __import__("pathlib").Path(base).rglob("*"))


@pytest.fixture
def benv(env):
    _write(env.path("bin.dat"), RAW)
    _write(env.path("big.dat"), BIG)
    return env


# ---- ACTION_SPEC_FS_READ_BYTES / registry --------------------------------------------------------------------------

def test_action_spec_is_the_same_authority_class_as_fs_read():
    assert ACTION_REGISTRY["fs.read_bytes"] == ActionSpec("FS_READ_BYTES", RiskLevel.LOW, "path", (), True)
    r, b = ACTION_REGISTRY["fs.read"], ACTION_REGISTRY["fs.read_bytes"]
    assert (r.risk, r.resource_arg, r.extra_args, r.binds_identity) == (b.risk, b.resource_arg, b.extra_args, b.binds_identity)
    assert ACTION_REGISTRY["fs.read"] == ActionSpec("FS_READ", RiskLevel.LOW, "path", (), True)  # unchanged


def test_registry_contains_exactly_the_two_read_executors():
    assert default_registry().actions() == ("fs.read", "fs.read_bytes")
    assert default_registry().lookup("fs.read_bytes").binds_identity is True


# ---- authorized read: raw bytes, bound handle, digest over raw bytes ------------------------------------------------

def test_authorized_read_returns_the_raw_bytes_through_the_bound_handle(benv):
    out = benv.run(_bytes_proposal(benv, "bin.dat"))
    assert out.final_decision.decision is Decision.ALLOW and benv.adapter_error is None
    res = benv.last_result
    assert res.ok and res.binary_output == RAW and res.output is None  # nothing collapsed into text
    assert res.result_digest == hashlib.sha256(RAW).hexdigest()  # over raw bytes, not base64 / text
    assert benv.adapter.effect_log()[0]["opcode"] == "FS_READ_BYTES"
    assert benv.adapter.effect_log()[0]["result_digest"] == res.result_digest
    assert res.observation.bound_handle_reads == 1 and res.observation.path_open_calls_during_exec == 0
    assert benv.spy.calls[0] == ("BOOT",) and benv.spy.calls[1][:2] == ("EXEC", "FS_READ_BYTES") and benv.spy.calls[-1] == ("HALT",)
    assert benv.oracle_violations() == []
    ev = [e for e in benv.store.events() if e.get("kind") == "EXECUTION_RESULT"][0]
    assert ev["ok"] is True and ev["result_digest"] == res.result_digest and ev["action"] == "fs.read_bytes"
    assert "binary_output" not in ev and RAW.hex() not in repr(ev)  # the evidence never carries the content


def test_files_larger_than_one_read_chunk_are_preserved_exactly(benv):
    benv.run(_bytes_proposal(benv, "big.dat"))
    assert benv.last_result.binary_output == BIG and benv.last_result.result_digest == hashlib.sha256(BIG).hexdigest()


def test_a_text_file_read_as_bytes_is_its_exact_bytes_and_fs_read_text_is_unchanged(benv):
    benv.run(_bytes_proposal(benv, "a.txt"))
    assert benv.last_result.binary_output == b"hello" and benv.last_result.output is None
    benv.run(benv.read("a.txt"))  # the text action: same behaviour as before
    assert benv.last_result.output == "hello" and benv.last_result.binary_output is None
    assert benv.last_result.result_digest == hashlib.sha256(b"hello").hexdigest()


def test_fs_read_text_still_refuses_non_utf8_it_was_not_changed_to_pass_bytes_through(benv):
    out = benv.run(benv.read("bin.dat"))
    assert out.final_decision.decision is Decision.ALLOW
    res = benv.last_result
    assert res.ok is False and "UnicodeDecodeError" in res.error and res.output is None and res.binary_output is None


# ---- authorization reuses the existing policy / root / provenance ----------------------------------------------------

@pytest.mark.parametrize("raw", ["../outside.txt", "../ws_evil/secret.txt", "sub/../../outside.txt"])
def test_unauthorized_binary_read_is_rejected_and_nothing_is_read(benv, raw):
    out = benv.run(_bytes_proposal(benv, raw))
    assert out.final_decision.decision is Decision.DENY and "resource_outside_root" in out.policy_decision.reason
    assert out.authorized_action is None and benv.adapter_calls == 0 and benv.last_result is None
    assert benv.adapter.effect_log() == [] and benv.spy.calls == [] and benv.bindings.read_log() == ()


def test_absolute_path_outside_the_root_is_rejected(benv):
    out = benv.run(_bytes_proposal(benv, os.path.join(benv.base, "outside.txt")))
    assert out.final_decision.decision is Decision.DENY and benv.adapter.effect_log() == []


def test_data_provenance_has_no_authority_for_binary_reads_either(benv):
    for src in ("file_content", "tool_output"):
        out = benv.run(_bytes_proposal(benv, "bin.dat", source=src))
        assert out.final_decision.decision is Decision.DENY and out.policy_decision.reason == "data_has_no_authority"
    assert benv.adapter.effect_log() == [] and benv.spy.calls == []


def test_missing_file_and_directory_are_not_authorized(benv):
    for raw in ("nope.bin", "sub"):
        out = benv.run(_bytes_proposal(benv, raw))
        assert out.final_decision.decision is Decision.DENY and out.authorized_action is None
    assert benv.adapter.effect_log() == []


def test_extra_args_are_rejected_by_the_closed_schema(benv):
    out = benv.run(benv.proposal("fs.read_bytes", {"path": "bin.dat", "encoding": "base64"}))
    assert out.final_decision.decision is Decision.DENY and "args_schema_mismatch" in out.policy_decision.reason
    assert benv.adapter.effect_log() == []


# ---- binding: open-first, same handle, swap safety, single use --------------------------------------------------------

def test_open_first_binding_is_pending_until_exec_and_released_on_deny(benv):
    action = _authorize_bytes(benv)
    assert action.resource_binding_id and action.resource_identity
    assert benv.bindings.open_calls() == 1
    benv.adapter.execute(action, benv.store)
    assert benv.bindings.open_calls() == 1  # EXEC opened nothing


def test_post_authorization_swap_cannot_change_which_bytes_are_read(benv):
    action = _authorize_bytes(benv)
    _write(benv.path("impostor.tmp"), b"IMPOSTOR\x00\xff")
    os.rename(benv.path("bin.dat"), benv.path("bin.orig"))
    os.replace(benv.path("impostor.tmp"), benv.path("bin.dat"))
    res = benv.adapter.execute(action, benv.store)
    assert res.binary_output == RAW and b"IMPOSTOR" not in res.binary_output
    assert res.observation.path_open_calls_during_exec == 0 and benv.oracle_violations() == []


def test_binary_authorization_is_single_use(benv):
    action = _authorize_bytes(benv)
    benv.adapter.execute(action, benv.store)
    with pytest.raises(AdapterError, match="replay_denied"):
        benv.adapter.execute(action, benv.store)
    assert len(benv.adapter.effect_log()) == 1


def test_executor_refuses_a_binding_whose_identity_is_not_the_authorized_one(benv):
    _write(benv.path("b.dat"), b"\x00bbb")
    table = BindingTable()
    bid_a, path_a, _ = table.open(benv.path("bin.dat"))
    _, _, ident_b = table.open(benv.path("b.dat"))
    sa = Standalone(table)
    rec = record_for("ev-0", "i", "fs.read_bytes", "FS_READ_BYTES", (), bid_a, path_a, ident_b)
    sa.issuer.issue(rec)
    with pytest.raises(ExecutorError, match="resource_identity_mismatch"):
        sa.runtime.submit(envelope_for(rec))
    assert table.read_log() == () and sa.registry.effects() == ()


def test_the_two_read_actions_do_not_cross_over(benv):
    """An authorization for fs.read_bytes cannot run on the fs.read executor (or the reverse): opcode must match."""
    table = BindingTable()
    bid, path, ident = table.open(benv.path("bin.dat"))
    sa = Standalone(table)
    rec = record_for("ev-0", "i", "fs.read", "FS_READ_BYTES", (), bid, path, ident)  # action/opcode disagree
    sa.issuer.issue(rec)
    with pytest.raises(Exception, match="opcode_mismatch"):
        sa.runtime.submit(envelope_for(rec))
    assert sa.registry.effects() == ()


# ---- no write effect, no extra authority -----------------------------------------------------------------------------

def test_no_write_effect_whatever_happens(benv):
    before = snapshot(benv.base)
    benv.run(_bytes_proposal(benv, "bin.dat"))
    benv.run(_bytes_proposal(benv, "../outside.txt"))
    benv.run(benv.read("bin.dat"))
    assert snapshot(benv.base) == before
    assert {e["opcode"] for e in benv.adapter.effect_log()} <= {"FS_READ_BYTES", "FS_READ"}
    assert benv.oracle_violations() == []


def test_governance_still_has_no_write_shell_or_network_executor():
    assert set(default_registry().actions()) == {"fs.read", "fs.read_bytes"}
    for forbidden in ("fs.write", "shell.exec", "net.fetch", "proc.run"):
        with pytest.raises(Exception):
            default_registry().lookup(forbidden)
