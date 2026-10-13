"""IAOS_AUTHORIZATION_CORRESPONDENCE_GATE: does what reached the execution correspond EXACTLY to what was authorized?

AuthorizationCorrespondenceMetric audits evidence only: it authorizes nothing, consults no Governance and
never reads a claim. EVIDENCE_SOURCE_INDEPENDENCE=PENDING / EVIDENCE_PRODUCER_TRUST=REQUIRED_PHASE_0:
a producer that lies consistently on BOTH sides is not detected by this metric.

The source-mutation mutants (M_*) live in tests/test_correspondence_mutants.py; each is killed by a test
NAMED here.
"""
import ast
import dataclasses
import pathlib
import types

import pytest

import audit
import governance
from assurance.chain import AuthorizationCorrespondenceChain
from audit.auditor import EvidenceAuditor
from audit.metrics.authorization_correspondence import AuthorizationCorrespondenceMetric
from audit.types import Status
from claims.producer import GATE_AUTHORIZATION_CORRESPONDENCE, produce_authorization_correspondence_claim
from claims.types import ClaimStatus
from evidence.exporter import export_authorization_correspondence_evidence
from evidence.types import AuthorizationCorrespondenceEvidence, AuthorizedAuthority, ExecutedAuthority
from governance.aep_adapter import AdapterError, AEPAdapter
from governance.aep_executors import AEPExecutorRegistry, Executor, RealFsReadExecutor
from governance.aep_runtime import AEPRuntime
from governance.consumption import ConsumeResult
from governance.envelope import EnvelopeRefused, validate_envelope
from test_envelope_contract import FailingHandoff, authorize

PASS, FAIL = Status.PASS, Status.FAIL
INSUFF, UNSUP = Status.INSUFFICIENT_EVIDENCE, Status.UNSUPPORTED
DIGEST = "d" * 64

SIDE = dict(authorization_id="ev-1", intent_id="i-1", opcode="FS_READ", resource_binding_id="b-1",
            authorization_digest=DIGEST)


def ev(authorized=None, executed=None, **kw):
    a = dict(SIDE, authorized_args=())
    x = dict(SIDE, executed_args=())
    a.update(authorized or {})
    x.update(executed or {})
    base = dict(schema_version=1, execution_id="x-1", authorized=AuthorizedAuthority(**a),
                executed=ExecutedAuthority(**x))
    base.update(kw)
    return AuthorizationCorrespondenceEvidence(**base)


def claim(status="PASS", execution_id="x-1", authorization_id="ev-1"):
    return produce_authorization_correspondence_claim(
        nominal_status=status, execution_id=execution_id, authorization_id=authorization_id)


def auditor():
    return EvidenceAuditor(expected_gate_id=GATE_AUTHORIZATION_CORRESPONDENCE)


def metric(evidence):
    return AuthorizationCorrespondenceMetric().evaluate(evidence)


# ---- A1: exact correspondence ------------------------------------------------------------

def test_a1_exact_correspondence_is_pass():
    r = metric(ev())
    assert r.status is PASS and r.violations == ()
    rep = auditor().audit(claim(), ev())
    assert rep.verified_result is PASS and rep.silent_escape is False and rep.contradictions == ()


def test_a1b_exact_nonempty_args_is_pass():
    args = (("mode", "r"), ("offset", "0"))
    assert metric(ev({"authorized_args": args}, {"executed_args": args})).status is PASS
    assert metric(ev({"authorized_args": [["mode", "r"]]}, {"executed_args": (("mode", "r"),)})).status is PASS  # list/tuple shape is normalized


# ---- divergence: every field, one at a time -> FAIL (never PASS, never INSUFFICIENT) -------------------

def assert_fail(evidence, violation):
    r = metric(evidence)
    assert r.status is FAIL and r.violations == (violation,), r
    rep = auditor().audit(claim(), evidence)
    assert rep.verified_result is FAIL
    assert rep.silent_escape is True and rep.contradictions == ("claimed PASS but verified FAIL",)


@pytest.mark.parametrize("opcode", ["FS_WRITE", "FS_READ ", "fs_read", "FS_READ2", "SHELL_EXEC"])
def test_opcode_drift_is_fail(opcode):
    assert_fail(ev(executed={"opcode": opcode}), "opcode_mismatch")


@pytest.mark.parametrize("executed_args", [
    (("content", "x"),),                      # widened
    (("mode", "r"), ("offset", "0")),         # widened (more than authorized)
    (("mode", "w"),),                         # same name, different value
    (("other", "r"),),                        # same value, different name
])
def test_args_widened_or_changed_is_fail(executed_args):
    assert_fail(ev({"authorized_args": (("mode", "r"),)}, {"executed_args": executed_args}), "args_mismatch")


@pytest.mark.parametrize("authorized_args, executed_args", [
    ((("mode", "r"), ("offset", "0")), (("mode", "r"),)),               # narrowed
    ((("mode", "r"), ("offset", "0")), (("offset", "0"), ("mode", "r"))),  # reordered: not canonically equal
    ((), (("mode", "r"),)),                                             # empty authorized, non-empty executed
    ((("mode", "r"),), ()),
])
def test_args_not_canonically_equal_is_fail(authorized_args, executed_args):
    assert_fail(ev({"authorized_args": authorized_args}, {"executed_args": executed_args}), "args_mismatch")


def test_binding_changed_is_fail():
    assert_fail(ev(executed={"resource_binding_id": "b-2"}), "binding_mismatch")


def test_intent_changed_is_fail():
    assert_fail(ev(executed={"intent_id": "i-evil"}), "intent_mismatch")


def test_authorization_id_changed_is_fail():
    assert_fail(ev(executed={"authorization_id": "ev-2"}), "authorization_id_mismatch")


def test_digest_difference_is_fail():
    assert_fail(ev(executed={"authorization_digest": "e" * 64}), "digest_mismatch")


def test_every_divergence_is_reported_not_just_the_first():
    r = metric(ev(executed={"opcode": "FS_WRITE", "intent_id": "x", "resource_binding_id": "b-2",
                            "authorization_digest": "f" * 64}))
    assert r.status is FAIL
    assert set(r.violations) == {"opcode_mismatch", "intent_mismatch", "binding_mismatch", "digest_mismatch"}


# ---- missing / malformed -> INSUFFICIENT_EVIDENCE (None never matches None) -----------------

SIDE_TEXT = ("authorization_id", "intent_id", "opcode", "resource_binding_id", "authorization_digest")


@pytest.mark.parametrize("field", SIDE_TEXT)
def test_missing_field_on_one_side_is_insufficient(field):
    for side in ("authorized", "executed"):
        r = metric(ev(**{side: {field: None}}))
        assert r.status is INSUFF and f"missing:{side}.{field}" in r.observations, (side, field, r)
        rep = auditor().audit(claim(), ev(**{side: {field: None}}))
        assert rep.verified_result is INSUFF and rep.silent_escape is True
        assert f"{side}.{field}" in rep.missing_evidence


@pytest.mark.parametrize("field", SIDE_TEXT)
def test_same_field_missing_on_both_sides_is_insufficient_never_a_match(field):
    both = dict(authorized={field: None}, executed={field: None})
    r = metric(ev(**both))
    assert r.status is INSUFF, "None == None must never count as a match"
    assert {f"missing:authorized.{field}", f"missing:executed.{field}"} <= set(r.observations)


def test_missing_args_on_either_or_both_sides_is_insufficient():
    for a, x in (({"authorized_args": None}, {}), ({}, {"executed_args": None}),
                 ({"authorized_args": None}, {"executed_args": None})):
        assert metric(ev(a, x)).status is INSUFF, (a, x)


@pytest.mark.parametrize("bad", ["", 7, ["list"], b"bytes", True])
def test_malformed_text_fields_are_insufficient(bad):
    assert metric(ev(executed={"opcode": bad})).status is INSUFF
    assert metric(ev(authorized={"intent_id": bad})).status is INSUFF


@pytest.mark.parametrize("bad_args", ["mode=r", (("mode",),), (("mode", "r", "x"),), (("mode", 1),), ((1, "r"),), (None,), 5])
def test_malformed_args_are_insufficient(bad_args):
    assert metric(ev(executed={"executed_args": bad_args})).status is INSUFF
    assert metric(ev(authorized={"authorized_args": bad_args})).status is INSUFF


def test_missing_whole_sides_is_insufficient():
    base = AuthorizationCorrespondenceEvidence(schema_version=1, execution_id="x-1")
    r = metric(base)
    assert r.status is INSUFF and {"missing:authorized", "missing:executed"} <= set(r.observations)
    only_a = dataclasses.replace(ev(), executed=None)
    assert metric(only_a).status is INSUFF and "missing:executed" in metric(only_a).observations
    only_x = dataclasses.replace(ev(), authorized=None)
    assert metric(only_x).status is INSUFF and "missing:authorized" in metric(only_x).observations


def test_missing_executed_side_is_insufficient_not_filled_from_claim_or_authorized():
    """CLAIM_NON_EVIDENTIARY: a claimed PASS, the claim's ids or the authorized side never stand in for the executed side."""
    evidence = dataclasses.replace(ev(), executed=None)
    rep = auditor().audit(claim("PASS"), evidence)
    assert rep.verified_result is INSUFF and rep.silent_escape is True
    assert "executed" in rep.missing_evidence
    wiped = dataclasses.replace(ev(), executed=ExecutedAuthority())  # nothing observed on the executed side
    rep2 = auditor().audit(claim("PASS"), wiped)
    assert rep2.verified_result is INSUFF and rep2.silent_escape is True


def test_insufficient_takes_precedence_over_a_divergence_elsewhere():
    r = metric(ev({"authorization_digest": None}, {"opcode": "FS_WRITE"}))
    assert r.status is INSUFF  # same precedence as ResourceBindingMetric: missing before invariants


def test_not_a_correspondence_evidence_is_insufficient():
    for junk in (None, {}, types.SimpleNamespace(), "x"):
        assert metric(junk).status is INSUFF


# ---- scope -> UNSUPPORTED --------------------------------------------------------------------

@pytest.mark.parametrize("version", [0, 2, 99])
def test_incompatible_schema_version_is_unsupported_not_fail(version):
    r = metric(ev(schema_version=version))
    assert r.status is UNSUP
    rep = auditor().audit(claim(), ev(schema_version=version))
    assert rep.verified_result is UNSUP and rep.silent_escape is True  # never preserves a nominal PASS


def test_unsupported_wins_over_missing_and_missing_schema_is_insufficient():
    assert metric(ev({"intent_id": None}, schema_version=2)).status is UNSUP
    assert metric(ev(schema_version=None)).status is INSUFF
    assert metric(ev(schema_version=True)).status is INSUFF


# ---- silent escape / claim handling -----------------------------------------------------------

def test_claimed_fail_with_verified_pass_is_a_contradiction_not_an_escape():
    rep = auditor().audit(claim("FAIL"), ev())
    assert rep.verified_result is PASS and rep.silent_escape is False
    assert rep.contradictions == ("claimed FAIL but verified PASS",)


def test_claim_cannot_select_the_metric_or_change_the_verdict():
    other = produce_authorization_correspondence_claim(nominal_status="PASS", execution_id="x-1", authorization_id="ev-1")
    for status in ("PASS", "FAIL", "INCONCLUSIVE"):
        c = dataclasses.replace(other, claimed_status=ClaimStatus(status))
        assert auditor().audit(c, ev()).verified_result is PASS
        assert auditor().audit(c, ev(executed={"opcode": "X"})).verified_result is FAIL
    wrong_gate = dataclasses.replace(other, gate_id="RESOURCE_BINDING")
    rep = auditor().audit(wrong_gate, ev())
    assert rep.verified_result is INSUFF and rep.metric_results[0].violations == ("CLAIM_GATE_MISMATCH",)


def test_subject_mismatch_is_not_evaluated_as_support():
    rep = auditor().audit(claim(authorization_id="ev-OTHER"), ev())
    assert rep.metric_results[0].violations == ("SUBJECT_MISMATCH",) and rep.verified_result is INSUFF
    rep = auditor().audit(claim(execution_id="x-OTHER"), ev())
    assert rep.metric_results[0].violations == ("SUBJECT_MISMATCH",)


# ---- DTO ---------------------------------------------------------------------------------

def test_dto_is_immutable_serializable_and_closed():
    e = ev({"authorized_args": (("mode", "r"),)}, {"executed_args": (("mode", "r"),)})
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.executed = None
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.executed.opcode = "x"
    again = AuthorizationCorrespondenceEvidence.from_dict(e.to_dict())
    assert again == e and metric(again).status is PASS
    import json
    assert AuthorizationCorrespondenceEvidence.from_dict(json.loads(json.dumps(e.to_dict()))) == e
    with pytest.raises(ValueError):
        AuthorizationCorrespondenceEvidence.from_dict({**e.to_dict(), "claim": "PASS"})
    d = e.to_dict()
    d["executed"]["smuggled"] = 1
    with pytest.raises(ValueError):
        AuthorizationCorrespondenceEvidence.from_dict(d)


def test_exporter_preserves_absence_never_a_safe_default():
    out = export_authorization_correspondence_evidence(types.SimpleNamespace(), types.SimpleNamespace())
    d = out.to_dict()
    assert d["execution_id"] is None
    assert all(v is None for v in d["authorized"].values()) and all(v is None for v in d["executed"].values())
    assert metric(out).status is INSUFF
    nothing = export_authorization_correspondence_evidence(None, None)
    assert metric(nothing).status is INSUFF


# ---- structure: the metric authorizes nothing, reads no claim, imports nothing from the system --------

AUD = pathlib.Path(audit.__file__).parent
GOV = pathlib.Path(governance.__file__).parent
METRIC_PY = AUD / "metrics" / "authorization_correspondence.py"


def test_metric_imports_only_stdlib_and_the_neutral_types_and_never_sees_a_claim():
    tree = ast.parse(METRIC_PY.read_text(encoding="utf-8"))
    mods = [(n.module or "", n.level) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    mods += [(a.name, 0) for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    for mod, level in mods:
        assert (level == 2 and mod == "types") or (level == 0 and mod in {"__future__", "json", "typing"}), (mod, level)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not {n for n in names if "claim" in n.lower()}
    assert not names & {"AuthorizedAction", "ExecutionEnvelope", "AuthorizationView", "AuthorizationIssuer",
                        "ConsumptionHandle", "governance", "static_mismatch", "authorizes_static"}
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"open", "eval", "exec", "compile", "__import__"}


def test_runtime_never_reads_the_executed_observation_back():
    for f in GOV.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        reads = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "executed"
                 and isinstance(n.ctx, ast.Load)]
        assert not reads, f.name  # an observation only: it feeds no decision


# ---- real runtime: AuthorizedAction -> adapter -> AEP runtime -> exporter -> metric -> report --------

def run_real(env, raw="a.txt"):
    action = authorize(env, raw)
    record = env.view.get_issued(action.authorization_id)
    result = env.adapter.execute(action, env.store)
    return action, record, result, AuthorizationCorrespondenceChain().audit_execution(result, record)


def test_real_good_execution_is_audited_pass(env):
    action, record, result, report = run_real(env)
    assert result.ok and report.claimed_result.claimed_status is ClaimStatus.PASS
    assert report.claimed_result.gate_id == GATE_AUTHORIZATION_CORRESPONDENCE
    assert report.verified_result is PASS and report.silent_escape is False
    evidence = export_authorization_correspondence_evidence(result, record)
    a, x = evidence.authorized, evidence.executed
    assert (a.authorization_id, a.intent_id, a.resource_binding_id) == (action.authorization_id, action.intent_id,
                                                                         action.resource_binding_id)
    assert x.opcode == "FS_READ" == a.opcode and x.executed_args == () == a.authorized_args  # observed, not copied
    assert x.authorization_digest == a.authorization_digest == record.authority_digest()
    assert evidence.execution_id == result.execution_id


def test_real_observation_is_recorded_by_the_runtime_not_copied_from_the_action(env):
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    seen = result.observation.executed
    assert seen is not None and seen is env.runtime.observations()[-1].executed
    assert seen.authorization_id == action.authorization_id and seen.intent_id == action.intent_id
    assert action.digest != seen.authorization_digest  # a different digest: the authority digest travelled in the envelope


def drift_adapter(**changes):
    class DriftAdapter(AEPAdapter):
        def _translate(self, action):
            return dataclasses.replace(super()._translate(action), **changes)

    return DriftAdapter


class NoStaticCheckRuntime(AEPRuntime):
    """A DEFECTIVE runtime (test double): it skips authorizes_static and executes whatever envelope it gets.

    It is NOT the product runtime and does not change it. It stands in for 'the pre-effect control failed'
    so the second, independent line (this metric) can be shown to see the divergence."""

    def submit(self, envelope):
        env = validate_envelope(envelope)
        record = self._view.get_issued(env.authorization_id)
        if record is None:
            raise EnvelopeRefused("no_valid_authorization")
        executor = self._validate_effect(env, record)
        if self._consume.consume_once(env.authorization_id, env.execution_id) is not ConsumeResult.CONSUMED:
            raise EnvelopeRefused("replay_denied")
        return self._exec(env, record, executor)


DRIFTS = {
    "intent": ({"intent_id": "i-evil"}, "intent_mismatch"),
    "digest": ({"authorization_digest": "0" * 64}, "digest_mismatch"),
}


@pytest.mark.parametrize("name", sorted(DRIFTS))
def test_product_runtime_drift_rejection_happens_before_any_effect(make_env, name):  # PRODUCT_RUNTIME_DRIFT_REJECTION
    changes, _ = DRIFTS[name]
    env = make_env(adapter_cls=drift_adapter(**changes))
    action = authorize(env)
    with pytest.raises(AdapterError):
        env.adapter.execute(action, env.store)
    assert [c for c in env.spy.calls if c[0] == "EXEC"] == []  # the real control stops it: nothing executed


@pytest.mark.parametrize("name", sorted(DRIFTS))
def test_audit_defense_in_depth_bad_when_the_precontrol_is_bypassed(make_env, name):  # AUDIT_DEFENSE_IN_DEPTH_BAD
    changes, violation = DRIFTS[name]
    env = make_env(adapter_cls=drift_adapter(**changes), runtime_cls=NoStaticCheckRuntime)
    action, record, result, report = run_real(env)
    assert result.ok and report.claimed_result.claimed_status is ClaimStatus.PASS  # the runtime claims PASS
    assert [c for c in env.spy.calls if c[0] == "EXEC"]  # it really executed the drifted authority
    assert report.verified_result is FAIL and report.silent_escape is True
    assert report.metric_results[0].violations == (violation,)


@pytest.mark.parametrize("field, value, violation", [
    ("opcode", "FS_WRITE", "opcode_mismatch"),                      # fs.read(A) -> executed as a write
    ("executed_args", (("content", "x"),), "args_mismatch"),         # args widened
    ("resource_binding_id", "b-someone-elses", "binding_mismatch"),  # fs.read(A) -> fs.read(B)
    ("intent_id", "i-other", "intent_mismatch"),
    ("authorization_id", "ev-other", "authorization_id_mismatch"),
    ("authorization_digest", "1" * 64, "digest_mismatch"),
])
def test_real_evidence_altered_after_export_is_fail_with_silent_escape(env, field, value, violation):
    """The executed side is altered after a real run (a buggy/lying producer or a swapped record)."""
    action, record, result, _ = run_real(env)
    real = export_authorization_correspondence_evidence(result, record)
    altered = dataclasses.replace(real, executed=dataclasses.replace(real.executed, **{field: value}))
    assert metric(real).status is PASS
    rep = auditor().audit(claim(execution_id=result.execution_id, authorization_id=record.authorization_id), altered)
    assert rep.verified_result is FAIL and rep.silent_escape is True
    assert violation in rep.metric_results[0].violations


def test_real_executed_side_of_another_authorization_does_not_correspond(env):
    """fs.read(A) authorized, but the executed facts are those of a different real authorization (B)."""
    _, rec_a, res_a, _ = run_real(env, "a.txt")
    _, rec_b, res_b, _ = run_real(env, "a.txt")
    swapped = dataclasses.replace(export_authorization_correspondence_evidence(res_a, rec_a),
                                  executed=export_authorization_correspondence_evidence(res_b, rec_b).executed)
    r = metric(swapped)
    assert r.status is FAIL
    assert {"authorization_id_mismatch", "intent_mismatch", "binding_mismatch", "digest_mismatch"} <= set(r.violations)


def test_real_missing_observation_is_insufficient_with_silent_escape(env):
    action = authorize(env)
    record = env.view.get_issued(action.authorization_id)
    result = env.adapter.execute(action, env.store)
    stripped = types.SimpleNamespace(ok=result.ok, execution_id=result.execution_id, observation=None)
    report = AuthorizationCorrespondenceChain().audit_execution(stripped, record)
    assert report.verified_result is INSUFF and report.silent_escape is True
    assert any(m.startswith("executed.") for m in report.missing_evidence)


def test_chain_owns_the_gate_and_the_claim_cannot_choose_it():
    chain = AuthorizationCorrespondenceChain()
    assert chain.expected_gate_id == GATE_AUTHORIZATION_CORRESPONDENCE
    with pytest.raises(ValueError):
        AuthorizationCorrespondenceChain(auditor=EvidenceAuditor(expected_gate_id="RESOURCE_BINDING"))


def test_metric_is_platform_independent_and_pass_does_not_depend_on_the_host(env):
    action, record, result, report = run_real(env)
    assert report.verified_result is PASS  # no Windows/NTFS scope: this property is about authority, not about the filesystem


# ---- what counts as "executed" ----------------------------------------------------------------
#
#   ExecutedAuthorityObservation  !=  consumed authorization
#                                 !=  validated envelope
#                                 !=  handoff attempted
#   ExecutedAuthorityObservation   =  the effect handler really ENTERED execution
#
# The handler is probed directly (``entered`` counts real calls to ``Executor.run``): the runtime's own
# ("EXEC", ...) call log is written BEFORE the handler is called, so it is not proof of entry.

class EnteredProbe(RealFsReadExecutor):
    def __init__(self):
        super().__init__()
        self.entered = 0

    def run(self, resource, args, identity, binding=None):
        self.entered += 1
        return super().run(resource, args, identity, binding)


class EnteredBoom(Executor):
    action, opcode, arg_names, binds_identity = "fs.read", "FS_READ", (), True

    def __init__(self):
        self.entered = 0

    def run(self, resource, args, identity, binding=None):
        self.entered += 1
        binding.close()
        raise RuntimeError("boom after entry")

    def effects(self):
        return ()


def executed_observations(env):
    return [o for o in env.runtime.observations() if o.executed is not None]


@pytest.mark.parametrize("name", sorted(DRIFTS))
def test_c_exec_observation_not_emitted_on_static_reject(make_env, name):
    changes, _ = DRIFTS[name]
    probe = EnteredProbe()
    env = make_env(adapter_cls=drift_adapter(**changes), registry=AEPExecutorRegistry({"fs.read": probe}))
    action = authorize(env)
    with pytest.raises(AdapterError):
        env.adapter.execute(action, env.store)  # authorizes_static rejects
    assert env.ledger.consumed_count() == 0  # not even consumed
    assert probe.entered == 0
    assert env.runtime.observations() == [] and executed_observations(env) == []


def test_c_exec_observation_not_emitted_on_handoff_failure(make_env):
    probe = EnteredProbe()
    env = make_env(runtime_cls=FailingHandoff, registry=AEPExecutorRegistry({"fs.read": probe}))
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    assert result.ok is False and "handoff exploded" in result.error
    assert env.ledger.consumed_by(action.authorization_id) == result.execution_id  # consume_once DID happen
    assert probe.entered == 0  # the handler was never called
    assert env.runtime.observations() == [] and executed_observations(env) == []
    assert result.observation is None


def test_c_exec_observation_exists_on_effect_failure(make_env):
    boom = EnteredBoom()
    env = make_env(registry=AEPExecutorRegistry({"fs.read": boom}))
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    assert result.ok is False and "boom after entry" in result.error
    assert boom.entered == 1  # the handler really started
    seen = executed_observations(env)
    assert len(seen) == 1
    ran = seen[0].executed
    assert (ran.authorization_id, ran.intent_id, ran.opcode, ran.executed_args) == (
        action.authorization_id, action.intent_id, "FS_READ", ())


def test_c_exec_observation_not_published_before_handler_entry(make_env):
    """A fault AFTER the handoff and BEFORE the handler is called leaves no ExecutedAuthorityObservation.

    Kills M_EXEC_OBSERVED_BEFORE_HANDLER: 'it reached the point before the effect' is not 'it executed'."""
    probe = EnteredProbe()
    env = make_env(registry=AEPExecutorRegistry({"fs.read": probe}))
    action = authorize(env)

    def fault():
        raise RuntimeError("fault between handoff and handler")

    env.bindings.open_calls = fault  # read by the runtime right before it calls the handler
    result = env.adapter.execute(action, env.store)
    assert result.ok is False and "fault between handoff and handler" in result.error
    assert probe.entered == 0
    assert env.runtime.observations() == [] and executed_observations(env) == []
