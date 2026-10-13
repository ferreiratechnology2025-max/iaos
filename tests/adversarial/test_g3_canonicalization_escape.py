import os

import pytest

from governance.contract import Decision


def _denied(env, raw):
    out = env.run(env.read(raw))
    assert out.final_decision.decision is Decision.DENY, raw
    assert out.policy_decision.reason == "resource_outside_root", raw
    assert env.spy.calls == []
    return out


def test_g3_dotdot_escape(env):
    out = _denied(env, "../outside.txt")
    assert out.resource == os.path.join(env.base, "outside.txt")


def test_g3_absolute_outside(env):
    _denied(env, os.path.join(env.base, "outside.txt"))


def test_g3_symlink_outside(env):
    link = env.path("link")
    try:
        os.symlink(os.path.join(env.base, "outside.txt"), link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    out = _denied(env, "link")
    assert out.resource == os.path.join(env.base, "outside.txt")


def test_g3_symlinked_directory_outside(env):
    link = env.path("dirlink")
    try:
        os.symlink(env.evil_dir, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    _denied(env, "dirlink/secret.txt")


def test_g3_sibling_with_root_as_string_prefix(env):
    # /…/ws_evil starts with /…/ws as a string but is outside the root.
    _denied(env, os.path.join(env.evil_dir, "secret.txt"))


def test_g3_positive_control_collapses_inside_root(env):
    out = env.run(env.read("sub/../a.txt"))
    assert out.final_decision.decision is Decision.ALLOW
    assert out.resource == env.path("a.txt")
