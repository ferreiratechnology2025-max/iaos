"""IAOS_CLAIMED_RESULT_GATE: a nominal claim is confronted with independent evidence.

CLAIM_NON_EVIDENTIARY, CLAIM_NON_AUTHORITY, CLAIM_DOES_NOT_SELECT_VERIFICATION_POLICY. The product
chain (claim producer + evidence exporter + auditor, composed in assurance/) is exercised with real
runtime results; no claim or evidence DTO is hand-built in the E2E tests.
AUDIT_DECISION_INDEPENDENCE=PASS; EVIDENCE_SOURCE_INDEPENDENCE=PENDING.
"""
import ast
import dataclasses
import json
import pathlib
import sys
import types

import pytest

import assurance
import audit
import claims
import evidence
import governance
from assurance.chain import AssuranceChain, audit_execution
from audit.auditor import EvidenceAuditor
from audit.metrics.resource_binding import ResourceBindingMetric
from audit.types import MetricResult, ResourceBindingEvidence, Status
from audit_support import ON_WINDOWS_NTFS
from claims.producer import (CLAIMANT_RUNTIME, GATE_RESOURCE_BINDING, claim_from_execution_result,
                             produce_claim, produce_resource_binding_claim)
from claims.types import ClaimedResult, ClaimProvenance, ClaimStatus
from evidence.exporter import export_resource_binding_evidence
from governance.aep_executors import AEPExecutorRegistry, FsReadResult, RealFsReadExecutor
from test_envelope_contract import ReopenByPathExecutor, authorize
from test_independent_audit import good

PASS, FAIL = Status.PASS, Status.FAIL
INSUFF, UNSUP = Status.INSUFFICIENT_EVIDENCE, Status.UNSUPPORTED
CPASS, CFAIL, CINC = ClaimStatus.PASS, ClaimStatus.FAIL, ClaimStatus.INCONCLUSIVE


def mk_claim(status=CPASS, execution_id="x-1", authorization_id="a-1", gate=GATE_RESOURCE_BINDING):
    return produce_claim(gate_id=gate, nominal_status=status, execution_id=execution_id,
                         authorization_id=authorization_id, claimant_component=CLAIMANT_RUNTIME)


class CountingResourceBindingMetric(ResourceBindingMetric):
    calls = 0

    def evaluate(self, evidence):
        self.calls += 1
        return super().evaluate(evidence)


class ConvenientMetric:
    """A lenient 'other' metric a claimant would love to be judged by."""

    name = "ConvenientMetric"
    calls = 0

    class validated_scope:  # noqa: N801 - minimal stand-in
        @staticmethod
        def label():
            return "any"

    def evaluate(self, evidence):
        self.calls += 1
        return MetricResult(self.name, PASS, "any")


def make(cls=EvidenceAuditor):
    """A product with a strict metric for the expected gate AND a convenient metric for another gate."""
    return cls({"RESOURCE_BINDING": CountingResourceBindingMetric(), "OTHER": ConvenientMetric()})


# id, claim, evidence, expected verified, expected silent_escape
AUDIT_CASES = [
    ("C1_GOOD_CLAIM_GOOD_EVIDENCE", mk_claim(CPASS), good(), PASS, False),
    ("C2_PASS_CLAIM_BAD_EVIDENCE", mk_claim(CPASS), good(path_open_calls_during_exec=1), FAIL, True),
    ("C3_PASS_CLAIM_MISSING_EVIDENCE", mk_claim(CPASS), good(observed_resource_identity=None), INSUFF, True),
    ("C4_SUBJECT_EXECUTION_MISMATCH", mk_claim(CPASS, execution_id="E1"), good(execution_id="E2"), INSUFF, True),
    ("C5_SUBJECT_AUTHORIZATION_MISMATCH", mk_claim(CPASS, authorization_id="A1"),
     good(authorization_id="A2"), INSUFF, True),
    ("C6_FAIL_CLAIM_GOOD_EVIDENCE", mk_claim(CFAIL), good(), PASS, False),
    ("C7_CLAIM_CANNOT_FILL_EVIDENCE", mk_claim(CPASS, authorization_id="a-1"),
     good(authorization_id=None), INSUFF, True),
    ("C10_WRONG_GATE_ID", mk_claim(CPASS, gate="OTHER"), good(), INSUFF, True),
    ("C11_UNKNOWN_GATE_ID", mk_claim(CPASS, gate="UNKNOWN"), good(), INSUFF, True),
]


def auditor_failures(auditor):
    bad = []
    for cid, claim, ev, verified, silent in AUDIT_CASES:
        rep = auditor.audit(claim, ev)
        if rep.verified_result is not verified or rep.silent_escape is not silent:
            bad.append(cid)
    return bad


PRODUCER_CASES = [
    ("C8_PRODUCER_PRESERVES_NOMINAL_FAIL", CFAIL, CFAIL),
    ("C8B_PRODUCER_PRESERVES_NOMINAL_PASS", CPASS, CPASS),
    ("C9_PRODUCER_PRESERVES_INCONCLUSIVE", CINC, CINC),
]


def producer_failures(produce):
    bad = []
    for cid, nominal, expected in PRODUCER_CASES:
        got = produce(nominal_status=nominal, execution_id="x-1", authorization_id="a-1")
        if got.claimed_status is not expected or got.gate_id != GATE_RESOURCE_BINDING:
            bad.append(cid)
    return bad


@pytest.mark.parametrize("cid,claim,ev,verified,silent", AUDIT_CASES, ids=[c[0] for c in AUDIT_CASES])
def test_audit_cases(cid, claim, ev, verified, silent):
    rep = EvidenceAuditor().audit(claim, ev)
    assert rep.verified_result is verified and rep.silent_escape is silent, rep
    rep2 = make().audit(claim, ev)  # also with a convenient metric available for another gate
    assert rep2.verified_result is verified and rep2.silent_escape is silent, rep2


def test_subject_mismatch_is_explicit_and_not_evaluated_as_support():
    for claim, ev in ((mk_claim(execution_id="E1"), good(execution_id="E2")),
                      (mk_claim(authorization_id="A1"), good(authorization_id="A2"))):
        rep = EvidenceAuditor().audit(claim, ev)
        assert rep.verified_result is INSUFF
        assert [r.violations for r in rep.metric_results] == [("SUBJECT_MISMATCH",)]
        assert all(r.metric != "ResourceBindingMetric" for r in rep.metric_results)


def test_wrong_or_unknown_gate_runs_no_metric_and_is_never_pass():
    for gate in ("OTHER", "UNKNOWN", "resource_binding", "X"):
        aud = make()
        rep = aud.audit(mk_claim(CPASS, gate=gate), good())
        assert rep.verified_result is INSUFF and rep.silent_escape is True, gate
        assert [r.violations for r in rep.metric_results] == [("CLAIM_GATE_MISMATCH",)], gate
        assert aud._metrics["RESOURCE_BINDING"].calls == 0 and aud._metrics["OTHER"].calls == 0, gate
    # a claim for the right gate still runs exactly the product's metric, never the convenient one
    aud = make()
    assert aud.audit(mk_claim(CPASS), good()).verified_result is PASS
    assert aud._metrics["RESOURCE_BINDING"].calls == 1 and aud._metrics["OTHER"].calls == 0


def test_expected_gate_without_a_metric_has_no_fallback():
    aud = EvidenceAuditor({"OTHER": ConvenientMetric()}, expected_gate_id="RESOURCE_BINDING")
    rep = aud.audit(mk_claim(CPASS), good())
    assert rep.verified_result is INSUFF and rep.silent_escape and rep.metric_results == ()
    assert aud._metrics["OTHER"].calls == 0
    aud = EvidenceAuditor({"RESOURCE_BINDING": ResourceBindingMetric()}, expected_gate_id="NOPE")
    assert aud.audit(mk_claim(CPASS, gate="NOPE"), good()).verified_result is INSUFF


def test_assurance_chain_owns_the_expected_gate():
    chain = AssuranceChain()
    assert chain.expected_gate_id == GATE_RESOURCE_BINDING
    with pytest.raises(ValueError):
        AssuranceChain(expected_gate_id="RESOURCE_BINDING", auditor=EvidenceAuditor(expected_gate_id="OTHER"))


@pytest.mark.parametrize("cid,nominal,expected", PRODUCER_CASES, ids=[c[0] for c in PRODUCER_CASES])
def test_producer_cases(cid, nominal, expected):
    got = produce_resource_binding_claim(nominal_status=nominal, execution_id="x-1", authorization_id="a-1")
    assert got.claimed_status is expected and got.gate_id == "RESOURCE_BINDING"


def test_silent_escape_truth_table():
    cases = [(CPASS, good(path_open_calls_during_exec=1), True), (CPASS, good(bound_handle_reads=None), True),
             (CPASS, good(filesystem="ReFS"), True), (CPASS, good(), False),
             (CFAIL, good(path_open_calls_during_exec=1), False), (CFAIL, good(), False),
             (CINC, good(path_open_calls_during_exec=1), False), (CINC, good(), False)]
    for status, ev, silent in cases:
        assert EvidenceAuditor().audit(mk_claim(status), ev).silent_escape is silent, (status, ev)


def test_claimed_status_never_changes_what_the_metric_concludes():
    for ev in (good(), good(path_open_calls_during_exec=1), good(observed_resource_identity=None)):
        results = {s: EvidenceAuditor().audit(mk_claim(s), ev).metric_results for s in (CPASS, CFAIL, CINC)}
        assert results[CPASS] == results[CFAIL] == results[CINC]


# ---- claim schema -----------------------------------------------------------------

def test_claim_is_immutable_closed_and_serializable():
    c = mk_claim()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.claimed_status = CFAIL
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.provenance.source_id = "other"
    assert ClaimedResult.from_dict(json.loads(json.dumps(c.to_dict()))) == c
    with pytest.raises(TypeError):
        ClaimedResult(**{**{f.name: getattr(c, f.name) for f in dataclasses.fields(c)}, "extra": 1})
    with pytest.raises(ValueError):
        ClaimedResult.from_dict({**c.to_dict(), "extra": 1})
    partial = c.to_dict()
    partial.pop("claimant_component")
    with pytest.raises(ValueError):
        ClaimedResult.from_dict(partial)
    for bad in ("UNSUPPORTED", "INSUFFICIENT_EVIDENCE", "MAYBE"):  # auditor vocabulary is not claimable
        with pytest.raises(ValueError):
            ClaimedResult.from_dict({**c.to_dict(), "claimed_status": bad})
    with pytest.raises(ValueError):
        ClaimedResult.from_dict({**c.to_dict(), "schema_version": 2})


def test_claim_carries_no_evidence_fields():
    names = {f.name for f in dataclasses.fields(ClaimedResult)}
    assert names == {"schema_version", "claim_id", "gate_id", "claimed_status", "execution_id",
                     "authorization_id", "claimant_component", "provenance"}
    forbidden = {"resource_binding_id", "authorized_resource_identity", "observed_resource_identity",
                 "path_open_calls_during_exec", "bound_handle_reads", "filesystem", "platform"}
    assert not names & forbidden


def test_producer_forms_provenance_and_does_not_invent_ids():
    c = claim_from_execution_result(types.SimpleNamespace(ok=True, execution_id="x-9"),
                                    types.SimpleNamespace(authorization_id="a-9"))
    assert (c.claimed_status, c.gate_id, c.claimant_component) == (CPASS, "RESOURCE_BINDING", CLAIMANT_RUNTIME)
    assert c.provenance == ClaimProvenance("EXECUTION_RESULT", "x-9") and c.execution_id == "x-9"
    inc = claim_from_execution_result(types.SimpleNamespace(execution_id="x-9"), types.SimpleNamespace(authorization_id="a-9"))
    assert inc.claimed_status is CINC  # nominal outcome not reported
    with pytest.raises(ValueError):  # a claim that cannot be formed is not formed
        claim_from_execution_result(types.SimpleNamespace(ok=True), types.SimpleNamespace(authorization_id="a-9"))


# ---- mutants ----------------------------------------------------------------------

class StatusDrivesMetric(EvidenceAuditor):  # M_CLAIM_STATUS_DRIVES_METRIC
    def _evaluate_metric(self, claim, metric, evidence):
        if claim.claimed_status is CPASS:
            return MetricResult(metric.name, PASS, metric.validated_scope.label())
        return super()._evaluate_metric(claim, metric, evidence)


class ClaimAsEvidence(EvidenceAuditor):  # M_CLAIM_AS_EVIDENCE
    def _evaluate_metric(self, claim, metric, evidence):
        if isinstance(evidence, ResourceBindingEvidence) and evidence.authorization_id is None:
            evidence = dataclasses.replace(evidence, authorization_id=claim.authorization_id)
        return super()._evaluate_metric(claim, metric, evidence)


class SubjectMismatchIgnored(EvidenceAuditor):  # M_SUBJECT_MISMATCH_IGNORED
    def _correlate(self, claim, evidence):
        return None


class ClaimSelectsMetric(EvidenceAuditor):  # M_CLAIM_SELECTS_METRIC
    def _check_gate(self, claim):
        return None

    def _select_metric(self, claim):
        return self._metrics.get(claim.gate_id)


class UnknownGateFallback(EvidenceAuditor):  # M_UNKNOWN_GATE_FALLBACK
    def _check_gate(self, claim):
        return None

    def _select_metric(self, claim):
        return self._metrics.get(claim.gate_id) or self._metrics.get(self.expected_gate_id)


def producer_forces_pass(**kw):  # M_PRODUCER_FORCES_PASS
    return produce_resource_binding_claim(**{**kw, "nominal_status": CPASS})


def test_sound_auditor_and_producer_pass_every_case():
    assert auditor_failures(make()) == []
    assert producer_failures(produce_resource_binding_claim) == []


def test_m_claim_status_drives_metric_is_killed():
    assert {"C2_PASS_CLAIM_BAD_EVIDENCE", "C3_PASS_CLAIM_MISSING_EVIDENCE"} <= set(auditor_failures(make(StatusDrivesMetric)))


def test_m_claim_as_evidence_is_killed():
    assert "C7_CLAIM_CANNOT_FILL_EVIDENCE" in auditor_failures(make(ClaimAsEvidence))


def test_m_subject_mismatch_ignored_is_killed():
    assert {"C4_SUBJECT_EXECUTION_MISMATCH", "C5_SUBJECT_AUTHORIZATION_MISMATCH"} <= set(
        auditor_failures(make(SubjectMismatchIgnored)))


def test_m_claim_selects_metric_is_killed():
    assert "C10_WRONG_GATE_ID" in auditor_failures(make(ClaimSelectsMetric))


def test_m_unknown_gate_fallback_is_killed():
    assert "C11_UNKNOWN_GATE_ID" in auditor_failures(make(UnknownGateFallback))


def test_m_producer_forces_pass_is_killed():
    assert "C8_PRODUCER_PRESERVES_NOMINAL_FAIL" in producer_failures(producer_forces_pass)


# ---- structural boundary (AST: lint of intent, not isolation from hostile Python) -----

def _dir(mod):
    return pathlib.Path(mod.__file__).parent


def _imports_of_source(src):
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append(n.module or "")
    return out


def _tops(directory):
    return {f.name: {m.split(".")[0] for m in _imports_of_source(f.read_text(encoding="utf-8")) if m}
            for f in directory.rglob("*.py")}


def producer_violations(src):
    """What claims/producer.py must never do: see, import or receive evidence/audit/authority."""
    bad = [m for m in _imports_of_source(src) if m.split(".")[0] in {"audit", "evidence", "governance", "assurance"}]
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {a.arg for n in ast.walk(tree) if isinstance(n, ast.arguments) for a in n.args + n.kwonlyargs} | \
            {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    bad += sorted(names & {"ResourceBindingEvidence", "AuditReport", "MetricResult", "EvidenceAuditor",
                           "ResourceBindingMetric", "export_resource_binding_evidence", "evidence"})
    return bad


def test_claims_boundary_imports():
    for name, tops in _tops(_dir(claims)).items():
        assert not tops & {"audit", "evidence", "governance", "assurance"}, name
        assert all(t in sys.stdlib_module_names or t == "claims" for t in tops), (name, tops)


def test_other_packages_do_not_import_the_claim_producer_or_depend_on_claims():
    for name, tops in _tops(_dir(governance)).items():
        assert "claims" not in tops, name  # governance/runtime
    for name, tops in _tops(_dir(evidence)).items():
        assert "claims" not in tops, name
    for f in _dir(audit).rglob("*.py"):
        for m in _imports_of_source(f.read_text(encoding="utf-8")):
            assert "producer" not in m, (f.name, m)
            if m.split(".")[0] == "claims":
                assert m == "claims.types", (f.name, m)  # neutral DTO only


def test_composition_level_is_the_only_one_importing_producer_exporter_and_auditor():
    imports = {m for f in _dir(assurance).rglob("*.py") for m in _imports_of_source(f.read_text(encoding="utf-8"))}
    assert {"claims.producer", "evidence.exporter", "audit.auditor"} <= imports


def test_claims_have_no_authority_mechanisms_or_effects():
    forbidden = {"AuthorizedAction", "ExecutionEnvelope", "AuthorizationIssuer", "AuthorizationView",
                 "ConsumptionLedger", "ConsumptionHandle", "BindingTable", "consume_once"}
    for f in _dir(claims).rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names & forbidden, (f.name, names & forbidden)
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not calls & {"open", "eval", "exec", "compile", "__import__"}, f.name


def _names_used_in(path, func_name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name)
    return {n.id for stmt in fn.body for n in ast.walk(stmt) if isinstance(n, ast.Name)}


def test_metrics_and_policy_selection_never_use_the_claim():
    for f in (_dir(audit) / "metrics").rglob("*.py"):
        names = {n.id for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))) if isinstance(n, ast.Name)}
        assert not {n for n in names if "claim" in n.lower()}, f.name
    auditor_py = _dir(audit) / "auditor.py"
    assert "claim" not in _names_used_in(auditor_py, "_evaluate_metric")  # metric sees evidence only
    assert "claim" not in _names_used_in(auditor_py, "_select_metric")  # CLAIM_DOES_NOT_SELECT_VERIFICATION_POLICY


def test_runtime_facing_producers_cannot_choose_the_gate():
    tree = ast.parse((_dir(claims) / "producer.py").read_text(encoding="utf-8"))
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name in {"produce_resource_binding_claim", "claim_from_execution_result"}):
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        assert "gate_id" not in params and "gate" not in params, fn.name


def test_m_claim_producer_reads_evidence_is_killed():
    src = (_dir(claims) / "producer.py").read_text(encoding="utf-8")
    assert producer_violations(src) == []
    assert producer_violations("from evidence.types import ResourceBindingEvidence\n" + src)
    assert producer_violations(src.replace("def produce_claim(*, ", "def produce_claim(*, evidence, "))
    assert producer_violations("import audit\n" + src)


# ---- exporter mutant causality: killed with SUBJECT_CORRELATION = PASS ------------------

class NoIdentityExecutor(RealFsReadExecutor):
    """Succeeds but does not report which identity it read."""

    def run(self, resource, args, identity, binding=None):
        out = super().run(resource, args, identity, binding)
        return FsReadResult(out.content, None)


def test_m_exporter_defaults_missing_as_safe_is_killed_by_the_evidence_itself_not_by_subject_mismatch(make_env):
    from test_audit_wiring import m_exporter_defaults_missing_as_safe

    env = make_env(registry=AEPExecutorRegistry({"fs.read": NoIdentityExecutor()}))
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    claim = claim_from_execution_result(result, action)
    pin = lambda ev: dataclasses.replace(ev, platform="win32", filesystem="NTFS")
    correct = export_resource_binding_evidence(result, action, volume_path=env.base)
    mutated = m_exporter_defaults_missing_as_safe(result, action, volume_path=env.base)
    # subject correlation PASSES for both: the ids are the real ones
    for ev in (correct, mutated):
        assert (ev.execution_id, ev.authorization_id) == (claim.execution_id, claim.authorization_id)
        assert EvidenceAuditor()._correlate(claim, pin(ev)) is None
    # the correct exporter preserves the missing observation; the mutant turns it into a certified PASS
    assert correct.observed_resource_identity is None
    assert EvidenceAuditor().audit(claim, pin(correct)).verified_result is INSUFF
    assert mutated.observed_resource_identity is not None
    rep = EvidenceAuditor().audit(claim, pin(mutated))
    assert rep.verified_result is PASS and "SUBJECT_MISMATCH" not in {v for r in rep.metric_results for v in r.violations}


# ---- E2E through the product chain (nothing hand-built) -----------------------------

def test_real_good_product_chain(env):
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    report = audit_execution(result, action, volume_path=env.base)
    c = report.claimed_result
    assert result.ok and c.claimed_status is CPASS and c.claimant_component == CLAIMANT_RUNTIME
    assert c.gate_id == GATE_RESOURCE_BINDING
    assert c.provenance == ClaimProvenance("EXECUTION_RESULT", result.execution_id)
    assert (c.execution_id, c.authorization_id) == (result.execution_id, action.authorization_id)
    if ON_WINDOWS_NTFS(env):
        assert report.verified_result is PASS and report.silent_escape is False
    else:  # outside the validated scope nothing is certified, and a PASS claim is a silent escape
        assert report.verified_result is UNSUP and report.silent_escape is True


def test_real_reopen_product_chain(make_env):
    env = make_env(registry=AEPExecutorRegistry({"fs.read": ReopenByPathExecutor()}))
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    report = audit_execution(result, action, volume_path=env.base)
    assert result.ok and report.claimed_result.claimed_status is CPASS
    assert report.silent_escape is True
    if ON_WINDOWS_NTFS(env):
        assert report.verified_result is FAIL
        assert "path_reopened_during_exec" in report.metric_results[0].violations
    else:
        assert report.verified_result is UNSUP
