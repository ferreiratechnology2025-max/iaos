import os

import pytest

from governance.contract import Decision


def test_g2_fs_read_with_canonical_path_outside_root(env):
    out = env.run(env.read(os.path.join(env.base, "outside.txt")))
    assert out.policy_decision.decision is Decision.DENY
    assert out.policy_decision.reason == "resource_outside_root"  # canonicalized fine, policy denied
    assert out.final_decision.decision is Decision.DENY
    assert out.resource == os.path.join(env.base, "outside.txt")
    assert env.spy.calls == []


def test_g2_policy_decision_and_final_decision_are_distinct_records(env):
    out = env.run(env.read(os.path.join(env.base, "outside.txt")))
    assert out.policy_decision is not out.final_decision
    ev = env.store.events()[0]
    assert ev["policy_decision"] == "DENY" and ev["final_decision"] == "DENY"
    assert "policy_decision" in ev and "final_decision" in ev


def test_g2_review_is_not_allow(env):
    out = env.run(env.write("n.txt", "d"))
    assert out.policy_decision.decision is Decision.ALLOW
    assert out.final_decision.decision is Decision.REVIEW
    assert out.authorized_action is None
