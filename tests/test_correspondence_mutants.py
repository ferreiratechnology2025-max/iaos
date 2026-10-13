"""Mutants for AuthorizationCorrespondenceMetric (and the paths that must never fill its evidence).

For each mutant: the pristine copy is GREEN on tests/test_authorization_correspondence.py, the mutated copy
is RED, and the NAMED test (not just any test) is among the failures. The real tree is never modified.

    M_OPCODE_DRIFT                         opcode compared by family, not exactly
    M_ARGS_WIDENED                         authorized args only need to be a subset of the executed ones
    M_BINDING_CHANGED                      resource_binding_id not compared
    M_INTENT_CHANGED                       intent_id not compared
    M_AUTHORIZATION_ID_CHANGED             authorization_id not compared
    M_DIGEST_IGNORED                       authorization_digest not compared
    M_MISSING_FIELD_AS_MATCH               an absent value counts as a valid, equal value
    M_CLAIM_USED_TO_FILL_EXECUTION_EVIDENCE the auditor fills the executed side from the claim / authorized side
    M_EXPORTER_FILLS_EXECUTED_FROM_AUTHORIZED the exporter copies the authorized side when nothing was observed
    M_EXEC_OBSERVED_BEFORE_HANDLER         the runtime publishes the executed observation before calling the handler

EVIDENCE_SOURCE_INDEPENDENCE=PENDING: a producer that lies consistently on both sides is NOT covered by any
mutant here (it is a documented limit, not a surviving mutant of this metric).
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
METRIC = "audit/metrics/authorization_correspondence.py"
AUDITOR = "audit/auditor.py"
EXPORTER = "evidence/exporter.py"
RUNTIME = "governance/aep_runtime.py"
TARGET = "tests/test_authorization_correspondence.py"

_FILL_FROM_CLAIM = '''        import dataclasses as _dc
        from evidence.types import ExecutedAuthority as _EA
        au = getattr(evidence, "authorized", None)
        ex = getattr(evidence, "executed", None)
        if au is not None and (ex is None or ex.authorization_id is None):
            evidence = _dc.replace(evidence, executed=_EA(
                authorization_id=claim.authorization_id, intent_id=au.intent_id, opcode=au.opcode,
                executed_args=au.authorized_args, resource_binding_id=au.resource_binding_id,
                authorization_digest=au.authorization_digest))
        return metric.evaluate(evidence)'''

_EXPORTER_FILL = '''    ran = _read(obs, "executed")
    if ran is None:
        class _C:
            authorization_id = _read(authorization, "authorization_id")
            intent_id = _read(authorization, "intent_id")
            opcode = _read(authorization, "opcode")
            executed_args = _read(authorization, "canonical_args")
            resource_binding_id = _read(authorization, "resource_binding_id")
            authorization_digest = _authority_digest(authorization)
        ran = _C'''

MUTANTS = {
    "M_OPCODE_DRIFT": (
        [(METRIC, "    if a.opcode != x.opcode:", '    if a.opcode.split("_")[0] != x.opcode.split("_")[0]:')],
        "test_opcode_drift_is_fail",
    ),
    "M_ARGS_WIDENED": (
        [(METRIC, "    if _canonical(a.authorized_args) != _canonical(x.executed_args):",
          "    if not set(a.authorized_args) <= set(x.executed_args):")],
        "test_args_widened_or_changed_is_fail",
    ),
    "M_BINDING_CHANGED": (
        [(METRIC, "    if a.resource_binding_id != x.resource_binding_id:", "    if False:")],
        "test_binding_changed_is_fail",
    ),
    "M_INTENT_CHANGED": (
        [(METRIC, "    if a.intent_id != x.intent_id:", "    if False:")],
        "test_intent_changed_is_fail",
    ),
    "M_AUTHORIZATION_ID_CHANGED": (
        [(METRIC, "    if a.authorization_id != x.authorization_id:", "    if False:")],
        "test_authorization_id_changed_is_fail",
    ),
    "M_DIGEST_IGNORED": (
        [(METRIC, "    if a.authorization_digest != x.authorization_digest:", "    if False:")],
        "test_digest_difference_is_fail",
    ),
    "M_MISSING_FIELD_AS_MATCH": (
        [(METRIC, '    return isinstance(v, str) and v != ""',
          '    return v is None or (isinstance(v, str) and v != "")')],
        "test_same_field_missing_on_both_sides_is_insufficient_never_a_match",
    ),
    "M_CLAIM_USED_TO_FILL_EXECUTION_EVIDENCE": (
        [(AUDITOR, "        return metric.evaluate(evidence)", _FILL_FROM_CLAIM)],
        "test_missing_executed_side_is_insufficient_not_filled_from_claim_or_authorized",
    ),
    "M_EXEC_OBSERVED_BEFORE_HANDLER": (
        [(RUNTIME, '            self.calls.append(("EXEC", executor.opcode, record.resource, args))',
          '            self.calls.append(("EXEC", executor.opcode, record.resource, args))\n'
          '            self._observations.append(EffectExecutionObservation(\n'
          '                env.execution_id, env.resource_binding_id, 0, 0, None, executed))')],
        "test_c_exec_observation_not_published_before_handler_entry",
    ),
    "M_EXPORTER_FILLS_EXECUTED_FROM_AUTHORIZED": (
        [(EXPORTER, '    ran = _read(obs, "executed")', _EXPORTER_FILL)],
        "test_real_missing_observation_is_insufficient_with_silent_escape",
    ),
}

DRIVERS = (
    "tests/adversarial/test_g10_oracle_teeth.py",
    "tests/test_aep_mutants.py",
    "tests/test_envelope_mutants.py",
    "tests/test_correspondence_mutants.py",
)


def _copy_project(dst):
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".git", ".venv", ".venv-win")
    for pkg in ("governance", "audit", "evidence", "claims", "assurance", "tests"):
        shutil.copytree(ROOT / pkg, dst / pkg, ignore=ignore)
    shutil.copy(ROOT / "pyproject.toml", dst / "pyproject.toml")
    for rel in DRIVERS:
        driver = dst / rel
        if driver.exists():
            driver.unlink()  # mutant drivers never run inside a mutant copy


def _pytest(cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TARGET],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )


def test_correspondence_pristine_copy_is_green(tmp_path):
    _copy_project(tmp_path)
    r = _pytest(tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_correspondence_mutant_is_killed_by_its_named_test(tmp_path, name):
    edits, relevant = MUTANTS[name]
    _copy_project(tmp_path)
    for rel, old, new in edits:
        target = tmp_path / rel
        src = target.read_text(encoding="utf-8")
        assert src.count(old) == 1, "mutation site not found exactly once: %s in %s" % (name, rel)
        target.write_text(src.replace(old, new, 1), encoding="utf-8")
    r = _pytest(tmp_path)
    assert r.returncode != 0, "mutant %s SURVIVED" % name
    failed = re.findall(r"FAILED \S+::(\w+)", r.stdout)
    assert relevant in failed, "%s killed, but not by %s (failed: %s)" % (name, relevant, failed)
    for rel, old, new in edits:  # the real tree still holds the original code
        assert old in (ROOT / rel).read_text(encoding="utf-8")
