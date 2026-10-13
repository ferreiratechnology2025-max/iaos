import hashlib
import os

from governance.contract import Decision


def test_h1_real_fs_read_inside_root(env):
    out = env.run(env.read("a.txt"))
    assert out.final_decision.decision is Decision.ALLOW
    assert out.policy_decision.decision is Decision.ALLOW
    assert env.spy.calls == [
        ("BOOT",),
        ("EXEC", "FS_READ", env.path("a.txt"), ()),
        ("HALT",),
    ]
    # real effect on the real filesystem
    assert env.last_result.ok and env.last_result.output == "hello"
    assert env.last_result.result_digest == hashlib.sha256(b"hello").hexdigest()
    assert [e["resource"] for e in env.adapter.effect_log()] == [env.path("a.txt")]
    kinds = [e["kind"] for e in env.store.events()]
    assert kinds == ["AUTHORIZATION", "EXECUTION_RESULT"]
    assert env.store.verify_chain()
    assert env.oracle_violations() == []


def test_h2_write_is_authorized_but_has_no_executor_in_this_phase(env):
    """fs.write can be authorized (Phase 0 contract) but Phase 1 registers only
    fs.read: execution fails closed before BOOT, with no effect."""
    proposal = env.write("new.txt", "data")
    out = env.run(proposal)
    assert out.final_decision.decision is Decision.REVIEW
    assert env.spy.calls == []

    digest = env.canonicalizer.canonicalize(proposal).digest()
    env.gate.grant("human-1", proposal.intent_id, digest)
    out = env.run(proposal)
    assert out.final_decision.decision is Decision.ALLOW  # authorization is fine
    assert str(env.adapter_error) == "unknown_executor"  # execution is not
    assert env.spy.calls == []
    assert env.adapter.effect_log() == []
    assert not os.path.exists(env.path("new.txt"))
    assert env.oracle_violations() == []
