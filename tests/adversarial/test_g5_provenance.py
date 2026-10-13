from governance.contract import Decision, Provenance


def test_g5_missing_provenance(env):
    out = env.run(env.proposal("fs.read", {"path": "a.txt"}, provenance=None))
    assert out.policy_decision.reason == "provenance_missing"
    assert out.final_decision.decision is Decision.DENY
    assert env.spy.calls == []


def test_g5_unknown_source(env):
    out = env.run(env.proposal("fs.read", {"path": "a.txt"}, provenance=Provenance("system", "i-x")))
    assert out.final_decision.decision is Decision.DENY


def test_g5_provenance_bound_to_another_intent(env):
    p = env.proposal("fs.read", {"path": "a.txt"}, intent="i-mine", provenance=Provenance("user", "i-other"))
    out = env.run(p)
    assert out.policy_decision.reason == "provenance_intent_mismatch"
    assert out.final_decision.decision is Decision.DENY
    assert env.spy.calls == []
