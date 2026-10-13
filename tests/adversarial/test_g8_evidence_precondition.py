import pytest

from governance.aep_adapter import AEPAdapter
from governance.contract import Decision
from governance.evidence import EventKind, InMemoryEvidenceStore


class AuthAppendFails(InMemoryEvidenceStore):
    def append(self, event):
        if event.kind is EventKind.AUTHORIZATION:
            raise OSError("disk full")
        return super().append(event)


def test_g8_authorization_append_failure_blocks_everything(make_env):
    env = make_env(store=AuthAppendFails())
    out = env.run(env.read("a.txt"))
    assert out.final_decision.decision is Decision.DENY
    assert out.final_decision.reason == "evidence_append_failed"
    assert out.authorized_action is None
    assert env.adapter_calls == 0
    assert [c for c in env.spy.calls if c[0] == "EXEC"] == []  # EXEC = 0
    assert env.spy.calls == []
    assert env.adapter.effect_log() == []  # effects = 0
    assert env.store.events() == []


def test_g8_failed_append_does_not_block_recording_a_denial(make_env):
    env = make_env(store=AuthAppendFails())
    out = env.run(env.read("../outside.txt"))
    assert out.final_decision.decision is Decision.DENY  # still denied, no raise
    assert env.adapter_calls == 0


def test_g8_adapter_refuses_anything_but_authorized_action(env):
    for bogus in (env.read("a.txt"), {"opcode": "FS_READ"}, None):
        with pytest.raises(TypeError):
            env.adapter.execute(bogus, env.store)
    assert env.spy.calls == []


def test_g8_authorization_event_precedes_authorized_action(env):
    out = env.run(env.read("a.txt"))
    first = env.store.events()[0]
    assert first["kind"] == "AUTHORIZATION"
    assert first["event_id"] == out.authorized_action.evidence_event_id
