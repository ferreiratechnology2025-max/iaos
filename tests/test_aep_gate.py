"""AEP integration gate: real fs.read effect and the authority property

    effect > 0  =>  an exactly matching AuthorizedAction exists, used once.

Every assertion about "no effect" looks at the REAL executor's effect log and,
where the file system is involved, at the file system itself — not only at a spy.
"""
import dataclasses
import hashlib
import os

import pytest

from governance import oracle
from governance.aep_adapter import AdapterError, AEPAdapter
from governance.aep_executors import UnknownExecutor, default_registry
from governance.aep_runtime import AEPRuntime, ExecRefused
from governance.envelope import EnvelopeRefused
from governance.handle_binding import BindingTable
from governance.contract import AuthorizedAction, Decision, action_digest
from governance.evidence import Event, EventKind, InMemoryEvidenceStore

from aep_support import Standalone, envelope_for, record_for


def forge(env, action="fs.read", opcode="FS_READ", resource=None, args=(), intent="forged"):
    """Test-only: write a self-consistent ALLOW authorization into the store and
    return the matching AuthorizedAction. Isolates ONE adapter layer at a time."""
    resource = resource or env.path("a.txt")
    digest = action_digest(intent, action, opcode, resource, args)
    ev_id = env.store.append(
        Event(
            EventKind.AUTHORIZATION,
            intent,
            {
                "policy_decision": "ALLOW", "policy_reason": "forged", "risk": "LOW",
                "approval_decision": "ALLOW", "budget_decision": "ALLOW",
                "final_decision": "ALLOW", "opcode": opcode, "resource": resource,
                "args": [list(p) for p in args], "digest": digest,
            },
        )
    )
    env.issuer.issue(record_for(ev_id, intent, action, opcode, args, resource=resource))
    return AuthorizedAction(intent, opcode, resource, tuple(args), digest, ev_id, action)


def authorized(env, raw="a.txt"):
    out = env.authorizer.authorize(env.read(raw))
    assert out.final_decision.decision is Decision.ALLOW
    return out.authorized_action


# ---- positive: real effect ------------------------------------------------------

def test_real_effect_is_the_file_content_not_a_canned_value(env):
    token = "real-" + os.urandom(8).hex()
    with open(env.path("fresh.txt"), "w") as fh:
        fh.write(token)
    out = env.run(env.read("fresh.txt"))
    assert out.final_decision.decision is Decision.ALLOW
    assert env.last_result.output == token
    assert env.last_result.result_digest == hashlib.sha256(token.encode()).hexdigest()
    assert env.adapter.effect_log()[0]["result_digest"] == env.last_result.result_digest
    assert env.oracle_violations() == []


def test_registered_executors_are_only_the_two_read_actions():
    assert default_registry().actions() == ("fs.read", "fs.read_bytes")


# ---- A1 no authorization --------------------------------------------------------

def test_a1_no_authorization_reaches_no_executor(env):
    real = authorized(env)
    Impostor = type("Impostor", (), {})
    impostor = Impostor()
    for field in dataclasses.fields(real):  # same fields, not an AuthorizedAction
        setattr(impostor, field.name, getattr(real, field.name))
    impostor.authorization_id = real.authorization_id
    for bogus in (None, {"opcode": "FS_READ"}, env.read("a.txt"), impostor):
        with pytest.raises(TypeError):
            env.adapter.execute(bogus, env.store)
    assert env.spy.calls == [] and env.adapter.effect_log() == []


def test_a1_forged_authorized_action_not_in_evidence_is_refused(env):
    fake = AuthorizedAction("i", "FS_READ", env.path("a.txt"), (), "d", "ev-99", "fs.read")
    with pytest.raises(AdapterError, match="authorization_not_in_evidence"):
        env.adapter.execute(fake, env.store)
    assert env.spy.calls == [] and env.adapter.effect_log() == []


def test_a1_runtime_itself_refuses_anything_but_an_envelope():
    sa = Standalone(BindingTable())
    for bogus in (object(), None, "fs.read", ("FS_READ", "/x", ())):
        with pytest.raises(EnvelopeRefused, match="invalid_envelope"):
            sa.runtime.submit(bogus)
    assert sa.runtime.calls == [] and sa.registry.effects() == ()


# ---- A2 wrong opcode ------------------------------------------------------------

def test_a2_authorized_fs_read_cannot_become_another_opcode(env):
    real = authorized(env)
    swapped = dataclasses.replace(real, opcode="FS_WRITE")
    with pytest.raises(AdapterError, match="authorization_mismatch"):
        env.adapter.execute(swapped, env.store)
    assert env.spy.calls == [] and env.adapter.effect_log() == []


def test_a2_registry_binds_action_to_its_own_opcode(env):
    forged = forge(env, action="fs.read", opcode="FS_WRITE")  # evidence agrees; registry does not
    with pytest.raises(AdapterError, match="opcode_mismatch"):
        env.adapter.execute(forged, env.store)
    assert env.spy.calls == [] and env.adapter.effect_log() == []


# ---- A3 wrong resource ----------------------------------------------------------

def test_a3_resource_substitution_cannot_reach_another_file(env):
    with open(env.path("b.txt"), "w") as fh:
        fh.write("b-secret")
    real = authorized(env, "a.txt")
    res = env.adapter.execute(real, env.store)
    assert res.ok and res.output == "hello"  # a.txt, never b.txt
    assert [e["resource"] for e in env.adapter.effect_log()] == [env.path("a.txt")]
    assert env.oracle_violations() == []


def test_a3_tampered_resource_is_refused_before_boot(env):
    with open(env.path("b.txt"), "w") as fh:
        fh.write("b-secret")
    real = authorized(env, "a.txt")
    tampered = dataclasses.replace(real, resource=env.path("b.txt"))
    with pytest.raises(AdapterError, match="authorization_mismatch"):
        env.adapter.execute(tampered, env.store)
    assert env.spy.calls == [] and env.adapter.effect_log() == []


# ---- A4 extra authority arg -----------------------------------------------------

def test_a4_extra_authority_arg_fails_closed_before_boot(env):
    forged = forge(env, args=(("follow_links", "yes"),))  # evidence agrees; shape does not
    with pytest.raises(AdapterError, match="args_shape"):
        env.adapter.execute(forged, env.store)
    assert env.spy.calls == [] and env.adapter.effect_log() == []


def test_a4_extra_arg_in_a_proposal_never_becomes_an_authorization(env):
    out = env.run(env.proposal("fs.read", {"path": "a.txt", "follow_links": "yes"}))
    assert out.final_decision.decision is Decision.DENY and out.authorized_action is None
    assert env.spy.calls == [] and env.adapter.effect_log() == []


# ---- A5 replay ------------------------------------------------------------------

def test_a5_replay_second_exec_denied_total_exec_one(env):
    real = authorized(env)
    first = env.adapter.execute(real, env.store)
    assert first.ok
    with pytest.raises(AdapterError, match="replay_denied"):
        env.adapter.execute(real, env.store)
    assert [c for c in env.spy.calls if c[0] == "EXEC"] == [("EXEC", "FS_READ", real.resource, ())]
    assert len(env.adapter.effect_log()) == 1
    assert sum(1 for e in env.store.events() if e["kind"] == "EXECUTION_RESULT") == 1
    assert env.oracle_violations() == []


def test_a5_runtime_has_its_own_single_execution_guard():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "f.txt")
        with open(path, "w") as fh:
            fh.write("x")
        table = BindingTable()
        bid, final_path, identity = table.open(path)
        sa = Standalone(table)
        rec = record_for("ev-0", "i", "fs.read", "FS_READ", (), bid, final_path, identity)
        sa.issuer.issue(rec)
        assert sa.runtime.submit(envelope_for(rec, "x-1")).content == "x"
        with pytest.raises(EnvelopeRefused, match="replay_denied"):
            sa.runtime.submit(envelope_for(rec, "x-2"))
        assert len(sa.registry.effects()) == 1


def test_a5_known_limit_replay_protection_is_local_to_one_ledger(env):
    """CHARACTERIZATION of a documented limit: a second AEP with its OWN ledger
    (another process, a restart) has no memory of consumed authorizations. The
    oracle still catches it after the fact. CROSS_ADAPTER_REPLAY_GATE=PENDING."""
    real = authorized(env)
    env.adapter.execute(real, env.store)
    other = Standalone(BindingTable())  # fresh ledger, same registry view
    other.runtime = AEPRuntime(other.registry, other.runtime._bindings, env.view, other.handle)
    AEPAdapter(other.runtime).execute(real, env.store)  # NOT refused: limit; binding is gone, so no real effect
    violations = oracle.check(env.store.events(), env.spy.calls)
    assert any(v.startswith("multiple_executions_for_authorization") for v in violations)


def test_a5_second_adapter_on_the_same_runtime_cannot_replay(env):
    real = authorized(env)
    env.adapter.execute(real, env.store)
    with pytest.raises(AdapterError, match="replay_denied"):
        AEPAdapter(env.spy, bindings=env.bindings).execute(real, env.store)


# ---- A6 unknown registry entry --------------------------------------------------

def test_a6_unknown_registry_entry_fails_before_exec_without_fallback(env):
    for action, opcode, args in (
        ("fs.write", "FS_WRITE", (("content", "x"),)),
        ("shell.exec", "SHELL_EXEC", ()),
        ("fs.read.alias", "FS_READ", ()),  # same opcode as a real one: must not fall back
    ):
        forged = forge(env, action=action, opcode=opcode, args=args, intent="f-" + action)
        with pytest.raises(AdapterError, match="unknown_executor"):
            env.adapter.execute(forged, env.store)
    assert env.spy.calls == [] and env.adapter.effect_log() == []
    assert not os.path.exists(env.path("x"))
    with pytest.raises(UnknownExecutor):
        default_registry().lookup("shell.exec")


# ---- A7 evidence failure --------------------------------------------------------

def test_a7_authorization_not_persisted_means_zero_real_executor_calls(make_env):
    class Failing(InMemoryEvidenceStore):
        def append(self, event):
            if event.kind is EventKind.AUTHORIZATION:
                raise OSError("disk full")
            return super().append(event)

    env = make_env(store=Failing())
    out = env.run(env.read("a.txt"))
    assert out.authorized_action is None and env.adapter_calls == 0
    assert env.adapter.effect_log() == [] and env.spy.calls == []


# ---- A8 correlation -------------------------------------------------------------

def test_a8_result_is_correlated_to_authorization_intent_and_execution(env):
    out = env.run(env.read("a.txt"))
    auth = [e for e in env.store.events() if e["kind"] == "AUTHORIZATION"][0]
    result = [e for e in env.store.events() if e["kind"] == "EXECUTION_RESULT"][0]
    assert result["authorization_id"] == auth["event_id"] == out.authorized_action.authorization_id
    assert result["intent_id"] == auth["intent_id"] == out.intent_id
    assert result["execution_id"] == env.last_result.execution_id
    assert (result["opcode"], result["resource"], result["args"]) == (
        auth["opcode"], auth["resource"], auth["args"])
    assert result["ok"] is True and result["result_digest"] == env.last_result.result_digest
    assert result["started_at"] <= result["finished_at"]
    assert auth["seq"] < result["seq"]  # append-only: result is a new, later event


# ---- A10 effect without a matching authorization (central) ----------------------

def _read_directly(adapter, path):
    table = BindingTable()
    bid, final_path, identity = table.open(path)
    adapter._victim_registry.lookup("fs.read").run(final_path, (), identity, table.take(bid))


class DirectExecutorMutant(AEPAdapter):
    """Reaches the real executor directly, skipping runtime and evidence."""

    def execute(self, action, store):
        _read_directly(self, self._victim)


def test_a10_effect_with_no_authorization_turns_the_oracle_red(make_env):
    env = make_env(adapter_cls=DirectExecutorMutant)
    env.adapter._victim = env.path("a.txt")
    env.adapter._victim_registry = env.executors
    env.adapter.execute(None, env.store)
    assert len(env.adapter.effect_log()) == 1  # a real effect happened
    violations = env.oracle_violations()
    assert any(v.startswith("effect_without_matching_authorization") for v in violations), violations


class IncompatibleEffectMutant(AEPAdapter):
    """Honest flow, then one extra real read of a DIFFERENT resource."""

    def execute(self, action, store):
        result = super().execute(action, store)
        _read_directly(self, self._victim)
        return result


def test_a10_effect_with_incompatible_authorization_turns_the_oracle_red(make_env):
    env = make_env(adapter_cls=IncompatibleEffectMutant)
    with open(env.path("b.txt"), "w") as fh:
        fh.write("b")
    env.adapter._victim = env.path("b.txt")
    env.adapter._victim_registry = env.executors
    env.run(env.read("a.txt"))
    violations = env.oracle_violations()
    assert any(v.startswith("effect_without_matching_authorization") for v in violations), violations


def test_a10_honest_flow_is_green_control(env):
    env.run(env.read("a.txt"))
    assert env.oracle_violations() == []
