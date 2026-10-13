import pytest

from governance.contract import Decision


@pytest.mark.parametrize("source", ["file_content", "tool_output"])
def test_g4_data_has_no_authority(env, source):
    out = env.run(env.read("a.txt", source=source))
    assert out.final_decision.decision is Decision.DENY
    assert out.policy_decision.reason == "data_has_no_authority"
    assert env.spy.calls == []


def test_g4_data_cannot_use_a_grant(env):
    p = env.write("n.txt", "d", source="file_content")
    env.gate.grant("human", p.intent_id, env.canonicalizer.canonicalize(p).digest())
    out = env.run(p)
    assert out.final_decision.decision is Decision.DENY
    assert env.spy.calls == []
