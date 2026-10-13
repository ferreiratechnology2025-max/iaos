"""Windows path semantics for the canonicalization surface.

Runs only on Windows (skipped elsewhere, explicitly). POSIX-only cases such as
/etc/passwd prove nothing about drive letters, backslashes or extended paths.
"""
import os
import sys

import pytest

from governance.contract import Decision

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-only path semantics"
)


def _out(env, raw):
    out = env.run(env.read(raw))
    assert env.adapter_calls == (1 if out.final_decision.decision is Decision.ALLOW else 0)
    return out


def test_win_absolute_backslash_inside_root_is_valid(env):
    out = _out(env, env.root + "\\a.txt")
    assert out.final_decision.decision is Decision.ALLOW
    assert out.resource == env.path("a.txt")


def test_win_relative_backslash_collapses_inside_root(env):
    out = _out(env, "sub\\..\\a.txt")
    assert out.final_decision.decision is Decision.ALLOW
    assert out.resource == env.path("a.txt")


def test_win_forward_slash_is_accepted_and_resolves_to_same_resource(env):
    out = _out(env, "sub/../a.txt")
    assert out.final_decision.decision is Decision.ALLOW
    assert out.resource == env.path("a.txt")


def test_win_case_insensitive_root_prefix_is_still_inside(env):
    out = _out(env, env.root.upper() + "\\A.TXT")
    assert out.final_decision.decision is Decision.ALLOW


@pytest.mark.parametrize(
    "raw",
    [
        "..\\outside.txt",
        "sub\\..\\..\\outside.txt",
        "../outside.txt",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "\\\\?\\C:\\Windows\\win.ini",  # extended-length prefix
        "\\Windows\\win.ini",  # root-relative on the current drive
    ],
)
def test_win_escapes_are_denied(env, raw):
    out = _out(env, raw)
    assert out.final_decision.decision is Decision.DENY, (raw, out.resource)
    assert env.spy.calls == []


def test_win_absolute_outside_same_drive_denied(env):
    out = _out(env, os.path.join(env.base, "outside.txt"))
    assert out.final_decision.decision is Decision.DENY
    assert out.policy_decision.reason == "resource_outside_root"


def test_win_sibling_with_root_as_string_prefix_denied(env):
    out = _out(env, env.evil_dir + "\\secret.txt")
    assert out.final_decision.decision is Decision.DENY


def test_win_drive_relative_never_reaches_a_path_outside_root(env):
    """'C:outside.txt' is drive-relative. Python joins it under the root when
    the drive matches, so it must resolve inside the root (ALLOW, effect on the
    canonical path) or be denied. It must never touch anything else."""
    out = env.run(env.read("C:outside.txt"))
    if out.final_decision.decision is Decision.ALLOW:
        assert os.path.commonpath([env.root, out.resource]) == env.root
        execs = [c for c in env.spy.calls if c[0] == "EXEC"]
        assert [c[2] for c in execs] == [out.resource]
    else:
        assert env.spy.calls == []
