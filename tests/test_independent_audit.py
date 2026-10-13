"""IAOS_INDEPENDENT_AUDIT_GATE (INDEPENDENCE_SCOPE=decision_logic_only).

Proves decision independence of the auditor and the ResourceBindingMetric over a
Windows/NTFS/fs.read scope. Does NOT prove evidence-source independence.
"""
import ast
import dataclasses
import json
import pathlib
import sys

import pytest

import audit
from audit.auditor import EvidenceAuditor
from audit.metrics.resource_binding import ResourceBindingMetric
from assurance.chain import audit_execution
from audit.types import AuditReport, MetricResult, ResourceBindingEvidence, Status
from claims.types import ClaimedResult, ClaimProvenance, ClaimStatus
from audit_support import ON_WINDOWS_NTFS
from test_envelope_contract import ReopenByPathExecutor, authorize

from evidence.exporter import export_resource_binding_evidence
from governance.aep_executors import AEPExecutorRegistry

PASS, FAIL = Status.PASS, Status.FAIL
INSUFF, UNSUP = Status.INSUFFICIENT_EVIDENCE, Status.UNSUPPORTED
GATE = "RESOURCE_BINDING"


def good(**kw):
    base = dict(execution_id="x-1", authorization_id="a-1", resource_binding_id="rb-1",
                authorized_resource_identity="1:2", observed_resource_identity="1:2",
                path_open_calls_during_exec=0, bound_handle_reads=1, effect_succeeded=True,
                platform="win32", filesystem="NTFS", action="fs.read")
    base.update(kw)
    return ResourceBindingEvidence(**base)


def claim(status, gate=GATE, execution_id="x-1", authorization_id="a-1"):
    return ClaimedResult(1, f"{gate}:{execution_id}", gate, ClaimStatus(status.value), execution_id, authorization_id,
                         "TEST_CLAIMANT", ClaimProvenance("EXECUTION_RESULT", execution_id))


# id, claimed, evidence, expected verified, expected silent_escape
ADVERSARIAL = [
    ("A1_GOOD_BOUND_READ", PASS, good(), PASS, False),
    ("A2_PATH_REOPEN", PASS, good(path_open_calls_during_exec=1), FAIL, True),
    ("A3_IDENTITY_MISMATCH", PASS, good(observed_resource_identity="9:9"), FAIL, True),
    ("A4_NO_BOUND_READ", PASS, good(bound_handle_reads=0), FAIL, True),
    ("A5_MISSING_OBSERVATION", PASS, good(observed_resource_identity=None), INSUFF, True),
    ("A6_HONEST_FAILURE", FAIL, good(effect_succeeded=False, bound_handle_reads=0), FAIL, False),
    ("A7_UNSUPPORTED_SCOPE", PASS, good(filesystem="ReFS"), UNSUP, True),
]


def failing_cases(metric):
    """Ids of adversarial cases the given metric gets wrong (empty = metric is sound)."""
    auditor = EvidenceAuditor({GATE: metric})
    bad = []
    for cid, claimed, evidence, verified, silent in ADVERSARIAL:
        rep = auditor.audit(claim(claimed), evidence)
        if rep.verified_result is not verified or rep.silent_escape is not silent:
            bad.append(cid)
    return bad


@pytest.mark.parametrize("cid,claimed,evidence,verified,silent", ADVERSARIAL, ids=[a[0] for a in ADVERSARIAL])
def test_adversarial_cases(cid, claimed, evidence, verified, silent):
    rep = EvidenceAuditor().audit(claim(claimed), evidence)
    assert rep.verified_result is verified and rep.silent_escape is silent, rep


def test_silent_escape_is_claimed_pass_without_verified_pass():
    for ev, expected in ((good(), False), (good(path_open_calls_during_exec=2), True),
                         (good(bound_handle_reads=None), True), (good(action="fs.write"), True)):
        assert EvidenceAuditor().audit(claim(PASS), ev).silent_escape is expected
    # claimed FAIL never produces a silent escape
    assert not EvidenceAuditor().audit(claim(FAIL), good(path_open_calls_during_exec=2)).silent_escape


def test_claimed_status_is_never_an_input_to_the_metric():
    ev = good(path_open_calls_during_exec=1)
    r_pass = EvidenceAuditor().audit(claim(PASS), ev)
    r_fail = EvidenceAuditor().audit(claim(FAIL), ev)
    assert r_pass.metric_results == r_fail.metric_results and r_pass.verified_result is FAIL


@pytest.mark.parametrize("field", ["execution_id", "authorization_id", "resource_binding_id",
                                   "authorized_resource_identity", "observed_resource_identity",
                                   "path_open_calls_during_exec", "bound_handle_reads",
                                   "effect_succeeded", "platform", "filesystem", "action"])
def test_missing_required_evidence_is_never_pass(field):
    rep = EvidenceAuditor().audit(claim(PASS), good(**{field: None}))
    assert rep.verified_result is INSUFF and rep.silent_escape and field in rep.missing_evidence


@pytest.mark.parametrize("change", [{"bound_handle_reads": True}, {"bound_handle_reads": -1},
                                    {"path_open_calls_during_exec": "0"}, {"path_open_calls_during_exec": False},
                                    {"effect_succeeded": 1}, {"observed_resource_identity": ""}])
def test_malformed_evidence_is_never_pass(change):
    assert EvidenceAuditor().audit(claim(PASS), good(**change)).verified_result is INSUFF


@pytest.mark.parametrize("change", [{"platform": "linux"}, {"platform": "darwin"}, {"filesystem": "ReFS"},
                                    {"filesystem": "ext4"}, {"action": "fs.write"}, {"action": "net.get"}])
def test_unsupported_scope_is_unsupported_not_pass_and_not_fail(change):
    rep = EvidenceAuditor().audit(claim(PASS), good(**change))
    assert rep.verified_result is UNSUP and rep.silent_escape
    assert rep.metric_results[0].scope == "win32/NTFS/fs.read"


def test_non_dto_evidence_and_unknown_gate_are_insufficient():
    assert EvidenceAuditor().audit(claim(PASS), {"bound_handle_reads": 1}).verified_result is INSUFF
    assert EvidenceAuditor().audit(claim(PASS, gate="OTHER"), good()).verified_result is INSUFF


def test_evidence_is_plain_serializable_and_closed():
    ev = good()
    again = ResourceBindingEvidence.from_dict(json.loads(json.dumps(ev.to_dict())))
    assert again == ev and EvidenceAuditor().audit(claim(PASS), again).verified_result is PASS
    with pytest.raises(ValueError):
        ResourceBindingEvidence.from_dict({**ev.to_dict(), "extra": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.bound_handle_reads = 5


def test_report_is_immutable_knowledge_not_authority():
    rep = EvidenceAuditor().audit(claim(PASS), good())
    assert isinstance(rep, AuditReport) and rep.verified_result is PASS
    with pytest.raises(dataclasses.FrozenInstanceError):
        rep.verified_result = FAIL


# ---- mutants ---------------------------------------------------------------------

class AlwaysPassMetric(ResourceBindingMetric):  # M_AUDITOR_ALWAYS_PASS
    def evaluate(self, evidence):
        return MetricResult(self.name, PASS, self.validated_scope.label())


class MissingAsSafeMetric(ResourceBindingMetric):  # M_MISSING_AS_SAFE
    def evaluate(self, evidence):
        if isinstance(evidence, ResourceBindingEvidence):
            evidence = dataclasses.replace(
                evidence,
                observed_resource_identity=evidence.observed_resource_identity or evidence.authorized_resource_identity,
            )
        return super().evaluate(evidence)


def test_sound_metric_passes_every_adversarial_case():
    assert failing_cases(ResourceBindingMetric()) == []


def test_m_auditor_always_pass_is_killed():
    killed = failing_cases(AlwaysPassMetric())
    assert {"A2_PATH_REOPEN", "A3_IDENTITY_MISMATCH", "A4_NO_BOUND_READ", "A5_MISSING_OBSERVATION",
            "A7_UNSUPPORTED_SCOPE"} <= set(killed)
    rep = EvidenceAuditor({GATE: AlwaysPassMetric()}).audit(claim(PASS), good(path_open_calls_during_exec=1))
    assert rep.verified_result is PASS and not rep.silent_escape  # what the mutant would wrongly report


def test_m_missing_as_safe_is_killed():
    assert "A5_MISSING_OBSERVATION" in failing_cases(MissingAsSafeMetric())


# ---- structural independence (AST: lint of intent, not isolation from hostile Python) ----

AUDIT_DIR = pathlib.Path(audit.__file__).parent
AUDIT_FILES = sorted(AUDIT_DIR.rglob("*.py"))
FORBIDDEN_NAMES = {"AuthorizedAction", "ExecutionEnvelope", "AuthorizationIssuer", "AuthorizationView",
                   "ConsumptionLedger", "ConsumptionHandle", "BindingTable", "consume_once", "issue"}


def _imports(tree):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(a.name, 0) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append((n.module or "", n.level))
    return out


def test_audit_imports_only_stdlib_and_itself():
    assert AUDIT_FILES
    for f in AUDIT_FILES:
        for module, level in _imports(ast.parse(f.read_text(encoding="utf-8"))):
            top = module.split(".")[0]
            assert level > 0 or top == "audit" or module in ("evidence.types", "claims.types") or top in sys.stdlib_module_names, (f.name, module)
            assert top not in {"governance", "tests"}, (f.name, module)


def test_audit_never_references_authority_or_runtime_symbols_and_has_no_effects():
    for f in AUDIT_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
                {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        assert not names & FORBIDDEN_NAMES, (f.name, names & FORBIDDEN_NAMES)
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not calls & {"open", "eval", "exec", "compile", "__import__"}, (f.name, calls)


def test_governance_does_not_import_audit():
    gov = pathlib.Path(__import__("governance").__file__).parent
    for f in gov.glob("*.py"):
        for module, _ in _imports(ast.parse(f.read_text(encoding="utf-8"))):
            assert module.split(".")[0] != "audit", f.name


# ---- against the real runtime (producer side lives in tests) ---------------------------

def test_real_good_bound_read_is_audited(env):
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    rep = audit_execution(result, action, volume_path=env.base)
    assert result.ok and result.observation.path_open_calls_during_exec == 0
    if ON_WINDOWS_NTFS(env):
        assert rep.verified_result is PASS and not rep.silent_escape
    else:  # outside the validated scope the metric must refuse to certify
        assert rep.verified_result is UNSUP and rep.silent_escape


def test_real_m_effect_reopen_by_path_is_audit_fail_with_silent_escape(make_env):
    env = make_env(registry=AEPExecutorRegistry({"fs.read": ReopenByPathExecutor()}))
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    assert result.ok  # the mutant "succeeds" and claims PASS
    ev = export_resource_binding_evidence(result, action, volume_path=env.base)
    rep = audit_execution(result, action, volume_path=env.base)
    assert rep.claimed_result.claimed_status is ClaimStatus.PASS and rep.silent_escape
    if ON_WINDOWS_NTFS(env):
        assert rep.verified_result is FAIL
        assert {"path_reopened_during_exec", "bound_handle_reads_not_one"} <= set(rep.metric_results[0].violations)
    else:
        assert rep.verified_result is UNSUP
