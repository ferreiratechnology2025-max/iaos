import dataclasses

import pytest

from governance.canonicalize import Canonicalizer
from governance.contract import ACTION_REGISTRY, ActionSpec, Decision, RiskLevel
from governance.budget import Budget


def test_g7_registry_is_read_only():
    with pytest.raises(TypeError):
        ACTION_REGISTRY["shell.exec"] = ActionSpec("SHELL", RiskLevel.LOW, "path")
    with pytest.raises(TypeError):
        del ACTION_REGISTRY["fs.read"]
    assert set(ACTION_REGISTRY) == {"fs.read", "fs.read_bytes", "fs.write"}


def test_g7_spec_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ACTION_REGISTRY["fs.write"].risk = RiskLevel.LOW


def test_g7_canonicalizer_refuses_mutable_registry(env):
    with pytest.raises(TypeError):
        Canonicalizer(env.resolver, {"shell.exec": ActionSpec("SHELL", RiskLevel.LOW, "path")})


def test_g7_budget_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Budget().forbidden_action_classes = frozenset()


def test_g7_proposal_args_are_immutable(env):
    p = env.read("a.txt")
    with pytest.raises(TypeError):
        p.args["path"] = "/etc/passwd"
