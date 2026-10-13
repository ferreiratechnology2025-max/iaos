"""IAOS_AUDIT_WIRING_GATE: runtime observables -> production exporter -> auditor.

The evidence DTO in every real case below comes from evidence.exporter (production code),
never assembled by hand. EVIDENCE_SOURCE_INDEPENDENCE=PENDING: the exporter trusts the
runtime's own observables, so a consistent lie in the exporter is not detected
(M_EXPORTER_HIDES_REOPEN_CONSISTENT = SURVIVES_EXPECTED).
"""
import ast
import dataclasses
import pathlib
import sys
import types

import pytest

import audit
import evidence
import governance
from audit.auditor import EvidenceAuditor
from audit.types import Status
from audit_support import ON_WINDOWS_NTFS
from claims.producer import claim_from_execution_result
from claims.types import ClaimStatus
from evidence.exporter import export_resource_binding_evidence
from evidence.types import ResourceBindingEvidence
from governance.aep_executors import AEPExecutorRegistry, FsReadResult, RealFsReadExecutor
from test_envelope_contract import ReopenByPathExecutor, authorize

PASS, FAIL = Status.PASS, Status.FAIL
INSUFF, UNSUP = Status.INSUFFICIENT_EVIDENCE, Status.UNSUPPORTED

GOV = pathlib.Path(governance.__file__).parent
AUD = pathlib.Path(audit.__file__).parent
EVD = pathlib.Path(evidence.__file__).parent


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(a.name, 0) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append((n.module or "", n.level))
    return out


def _tops(directory):
    return {f.name: {m.split(".")[0] for m, lvl in _imports(f) if lvl == 0} for f in directory.rglob("*.py")}


# ---- structural separation -------------------------------------------------------

def test_runtime_and_governance_import_neither_audit_nor_evidence():
    for name, tops in _tops(GOV).items():
        assert not tops & {"audit", "evidence"}, name


def test_audit_imports_neither_runtime_nor_governance_nor_the_exporter():
    for f in AUD.rglob("*.py"):
        for module, lvl in _imports(f):
            assert module.split(".")[0] not in {"governance", "tests"}, (f.name, module)
            assert "exporter" not in module, (f.name, module)
            if module.split(".")[0] == "evidence":
                assert module == "evidence.types", (f.name, module)  # neutral DTOs only


def test_evidence_imports_only_stdlib_and_itself_and_never_audit_or_governance():
    for f in EVD.rglob("*.py"):
        for module, lvl in _imports(f):
            top = module.split(".")[0]
            assert lvl > 0 or top in sys.stdlib_module_names or top == "evidence", (f.name, module)
            assert top not in {"audit", "governance"}, (f.name, module)


def test_no_circular_dependency_between_audit_and_evidence():
    assert "audit" not in {t for ts in _tops(EVD).values() for t in ts}


DECISION_NAMES = {"Status", "PASS", "FAIL", "UNSUPPORTED", "INSUFFICIENT_EVIDENCE", "MetricResult",
                  "AuditReport", "ClaimedResult", "silent_escape", "evaluate", "audit", "violations"}


def test_exporter_contains_no_decision_logic_and_no_effects():
    tree = ast.parse((EVD / "exporter.py").read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare):  # the only comparisons allowed are `x is None` presence checks
            assert all(isinstance(op, (ast.Is, ast.IsNot)) for op in n.ops), ast.dump(n)
            assert all(isinstance(c, ast.Constant) and c.value is None for c in n.comparators), ast.dump(n)
        assert not isinstance(n, (ast.BoolOp, ast.IfExp, ast.Assert)), ast.dump(n)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & DECISION_NAMES, names & DECISION_NAMES
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"open", "eval", "exec", "compile", "__import__"}


def test_dto_is_immutable():
    ev = ResourceBindingEvidence()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.bound_handle_reads = 1


# ---- the exporter preserves absence ------------------------------------------------

def test_exporter_preserves_absence_never_a_safe_default():
    ev = export_resource_binding_evidence(types.SimpleNamespace(), types.SimpleNamespace())
    d = ev.to_dict()
    for k in ("execution_id", "authorization_id", "resource_binding_id", "authorized_resource_identity",
              "observed_resource_identity", "path_open_calls_during_exec", "bound_handle_reads",
              "effect_succeeded", "action", "filesystem"):
        assert d[k] is None, k


# ---- real end-to-end: AuthorizedAction -> runtime -> exporter -> metric -> report ----------

def run_real(env):
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    ev = export_resource_binding_evidence(result, action, volume_path=env.base)
    report = EvidenceAuditor().audit(claim_from_execution_result(result, action), ev)
    return action, result, ev, report


def as_ntfs(ev):
    """Pin the host scope so mutation checks do not depend on the machine running them."""
    return dataclasses.replace(ev, platform="win32", filesystem="NTFS")


def audit_ntfs(result, action, ev):
    return EvidenceAuditor().audit(claim_from_execution_result(result, action), as_ntfs(ev))


def test_real_good_execution_is_audited_pass(env):
    action, result, ev, report = run_real(env)
    assert result.ok and report.claimed_result.claimed_status is ClaimStatus.PASS
    assert ev.path_open_calls_during_exec == 0 and ev.bound_handle_reads == 1
    assert ev.authorized_resource_identity == ev.observed_resource_identity == action.resource_identity
    assert audit_ntfs(result, action, ev).verified_result is PASS and not audit_ntfs(result, action, ev).silent_escape
    if ON_WINDOWS_NTFS(env):
        assert report.verified_result is PASS and report.silent_escape is False
        assert ev.platform == "win32" and ev.filesystem == "NTFS" and ev.action == "fs.read"
    else:
        assert report.verified_result is UNSUP  # outside the validated scope: never certified


def test_real_reopen_execution_is_audit_fail_with_silent_escape(make_env):
    env = make_env(registry=AEPExecutorRegistry({"fs.read": ReopenByPathExecutor()}))
    action, result, ev, report = run_real(env)
    assert result.ok and report.claimed_result.claimed_status is ClaimStatus.PASS  # runtime claims PASS
    assert ev.path_open_calls_during_exec > 0  # exported from the real observation, not hand-built
    pinned = audit_ntfs(result, action, ev)
    assert pinned.verified_result is FAIL and pinned.silent_escape
    assert "path_reopened_during_exec" in pinned.metric_results[0].violations
    if ON_WINDOWS_NTFS(env):
        assert report.verified_result is FAIL and report.silent_escape
    else:
        assert report.silent_escape  # UNSUPPORTED is still not PASS


class NoIdentityExecutor(RealFsReadExecutor):
    """Succeeds but does not report which identity it read (an observable that is not produced)."""

    def run(self, resource, args, identity, binding=None):
        out = super().run(resource, args, identity, binding)
        return FsReadResult(out.content, None)


def test_real_missing_observation_is_insufficient_evidence(make_env):
    env = make_env(registry=AEPExecutorRegistry({"fs.read": NoIdentityExecutor()}))
    action, result, ev, report = run_real(env)
    assert result.ok and ev.observed_resource_identity is None  # real absence, preserved by the exporter
    pinned = audit_ntfs(result, action, ev)
    assert pinned.verified_result is INSUFF and pinned.silent_escape
    if ON_WINDOWS_NTFS(env):
        assert report.verified_result is INSUFF and report.silent_escape


def test_missing_whole_observation_is_insufficient_evidence(env):
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    stripped = types.SimpleNamespace(ok=result.ok, execution_id=result.execution_id, observation=None)
    ev = export_resource_binding_evidence(stripped, action, volume_path=env.base)
    assert ev.path_open_calls_during_exec is None and ev.bound_handle_reads is None
    rep = audit_ntfs(stripped, action, ev)
    assert rep.verified_result is INSUFF and rep.silent_escape


# ---- exporter mutants ----------------------------------------------------------------

def m_exporter_defaults_missing_as_safe(result, action, **kw):
    """M_EXPORTER_DEFAULTS_MISSING_AS_SAFE: absence becomes a safe-looking value."""
    ev = export_resource_binding_evidence(result, action, **kw)
    fill = {"execution_id": "x", "authorization_id": "a", "resource_binding_id": "rb",
            "authorized_resource_identity": "i",
            "observed_resource_identity": ev.authorized_resource_identity or "i",
            "path_open_calls_during_exec": 0, "bound_handle_reads": 1, "effect_succeeded": True}
    return dataclasses.replace(ev, **{k: v for k, v in fill.items() if getattr(ev, k) is None})


@pytest.mark.parametrize("kind", ["identity_not_reported", "observation_absent"])
def test_m_exporter_defaults_missing_as_safe_is_killed(make_env, kind):
    registry = AEPExecutorRegistry({"fs.read": NoIdentityExecutor()}) if kind == "identity_not_reported" else None
    env = make_env(registry=registry) if registry is not None else make_env()
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    if kind == "observation_absent":
        result = types.SimpleNamespace(ok=result.ok, execution_id=result.execution_id, observation=None)
    correct = export_resource_binding_evidence(result, action, volume_path=env.base)
    assert audit_ntfs(result, action, correct).verified_result is INSUFF  # correct exporter: never PASS
    mutated = m_exporter_defaults_missing_as_safe(result, action, volume_path=env.base)
    rep = audit_ntfs(result, action, mutated)
    assert mutated != correct, "M_EXPORTER_DEFAULTS_MISSING_AS_SAFE SURVIVED"  # the mutant visibly changes the evidence
    if kind == "identity_not_reported":
        assert rep.verified_result is PASS  # absence turned into a certified PASS: a red suite flags the mutant
    else:  # a fabricated execution_id also contradicts the claim's subject (defense in depth)
        assert rep.metric_results[0].violations == ("SUBJECT_MISMATCH",)

def test_m_exporter_hides_reopen_partial_is_still_caught_by_redundant_facts(make_env):
    """Literal M_EXPORTER_HIDES_REOPEN (only path_open_calls -> 0): bound_handle_reads=0 still betrays it."""
    env = make_env(registry=AEPExecutorRegistry({"fs.read": ReopenByPathExecutor()}))
    action, result, ev, _ = run_real(env)
    assert ev.path_open_calls_during_exec > 0
    hidden = dataclasses.replace(ev, path_open_calls_during_exec=0)
    rep = audit_ntfs(result, action, hidden)
    assert rep.verified_result is FAIL and rep.silent_escape  # KILLED, by the redundant fact


def test_m_exporter_hides_reopen_consistent_survives_expected(make_env):
    """A consistent lie (no reopen AND one bound read) is not detectable by this auditor.

    M_EXPORTER_HIDES_REOPEN=SURVIVES_EXPECTED  ->  EVIDENCE_SOURCE_INDEPENDENCE=PENDING.
    This test characterizes the limit; it does not make it pass-by-construction elsewhere.
    """
    env = make_env(registry=AEPExecutorRegistry({"fs.read": ReopenByPathExecutor()}))
    action, result, ev, _ = run_real(env)
    assert ev.path_open_calls_during_exec > 0
    lie = dataclasses.replace(ev, path_open_calls_during_exec=0, bound_handle_reads=1)
    rep = audit_ntfs(result, action, lie)
    assert rep.verified_result is PASS and rep.silent_escape is False  # the mutant SURVIVES
