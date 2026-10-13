import pytest

from governance.contract import CanonicalAction, Decision, Provenance
from governance.budget import Budget


@pytest.mark.parametrize("action", ["fs.delete", "fs.read.outside", "shell.exec", "", "FS.READ"])
def test_g1_unknown_action_denied_and_never_executed(env, action):
    out = env.run(env.proposal(action, {"path": "a.txt"}))
    assert out.final_decision.decision is Decision.DENY
    assert out.policy_decision.reason.startswith("canonicalization_rejected")
    assert out.authorized_action is None
    assert env.spy.calls == []
    assert out.budget_decision is not None


@pytest.mark.parametrize(
    "args",
    [{}, {"path": "a.txt", "approved": "yes"}, {"path": 3}, {"path": ""}, {"path": "a\x00b"}],
)
def test_g1_closed_args_schema(env, args):
    out = env.run(env.proposal("fs.read", args))
    assert out.final_decision.decision is Decision.DENY
    assert env.spy.calls == []


def test_g1_budget_forbids_action_class_even_if_registry_were_bypassed():
    canon = CanonicalAction("i", "shell.exec", "SHELL", "/x", (), Provenance("user", "i"))
    assert Budget().evaluate(canon).decision is Decision.DENY
