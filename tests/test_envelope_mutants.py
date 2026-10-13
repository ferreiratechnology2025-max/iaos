"""Mutants for the ExecutionEnvelope contract, run on an isolated copy.

For each mutant: the pristine copy is GREEN, the mutated copy is RED, and the
NAMED test (and not just any test) is among the failures. The real tree is never
modified.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

ENVELOPE = "governance/envelope.py"
RUNTIME = "governance/aep_runtime.py"
ADAPTER = "governance/aep_adapter.py"
REGISTRY = "governance/auth_registry.py"
LEDGER = "governance/consumption.py"
EXECUTORS = "governance/aep_executors.py"

EXTRAS_CHECK = '    if set(raw) != _FIELD_SET:\n        raise EnvelopeRefused("invalid_envelope")'
EXTRAS_CHECK_OFF = '    if False:\n        raise EnvelopeRefused("invalid_envelope")'
ADAPTER_PORT = "        self._aep = aep  # AEP port: ``submit`` only"

MUTANTS = {
    "M_EXTRA_FIELD_IGNORED": (
        [(ENVELOPE, EXTRAS_CHECK, EXTRAS_CHECK_OFF)],
        "test_c11_extra_envelope_field_is_rejected_before_lookup_and_consume",
    ),
    "M_DIGEST_NOT_CHECKED": (
        [(ENVELOPE,
          '    if envelope.authorization_digest != record.authority_digest():\n        return "digest_mismatch"',
          '    if False:\n        return "digest_mismatch"')],
        "test_c12_authorization_digest_mismatch_is_rejected_without_consuming",
    ),
    "M_METADATA_CHANGES_TARGET": (
        [(ENVELOPE, EXTRAS_CHECK, EXTRAS_CHECK_OFF),
         (ENVELOPE, "        resource_binding_id=binding,\n",
          '        resource_binding_id=raw.get("metadata_binding_id", binding),\n')],
        "test_c17_metadata_channel_cannot_change_the_target",
    ),
    "M_ADAPTER_GETS_ISSUER": (
        [(REGISTRY, "    return issuer, view",
          '    globals()["_LEAK_ISSUER"] = issuer\n    return issuer, view'),
         (ADAPTER, ADAPTER_PORT,
          ADAPTER_PORT + "\n        from . import auth_registry as _ar\n        self._leak = _ar._LEAK_ISSUER")],
        "test_c18_adapter_has_no_issuer_capability",
    ),
    "M_ADAPTER_GETS_CONSUMPTION_HANDLE": (
        [(LEDGER, "    return ledger, handle",
          '    globals()["_LEAK_HANDLE"] = handle\n    return ledger, handle'),
         (ADAPTER, ADAPTER_PORT,
          ADAPTER_PORT + "\n        from . import consumption as _c\n        self._leak = _c._LEAK_HANDLE")],
        "test_c19_adapter_has_no_consumption_capability",
    ),
    "M_CHECK_THEN_CONSUME": (
        [(LEDGER,
          "        with self._lock:\n            if key in self._state:\n                return False\n"
          "            self._state[key] = value\n            return True",
          "        if key in self._state:\n            return False\n"
          "        import time as _t; _t.sleep(0.001)\n"
          "        self._state[key] = value\n        return True")],
        "test_c15_concurrent_consume_once_has_exactly_one_winner_real_threads",
    ),
    "M_CONSUME_BEFORE_STATIC": (
        [(RUNTIME, "        mismatch = static_mismatch(record, env)  # 4. pure",
          "        self._consume.consume_once(env.authorization_id, env.execution_id)\n"
          "        mismatch = static_mismatch(record, env)  # 4. pure")],
        "test_static_failure_does_not_consume_and_authorization_stays_available",
    ),
    "M_UNCONSUME_ON_EFFECT_FAILURE": (
        [(RUNTIME, "                raise  # post-consume failure: authorization stays CONSUMED (no rollback)",
          "                self._consume._ledger._state.pop(env.authorization_id, None)\n                raise")],
        "test_c16a_effect_failure_after_consume_burns_the_authorization",
    ),
    "M_EFFECT_REOPEN_BY_PATH": (
        [(EXECUTORS,
          "            data = binding.read_all()  # the SAME handle opened during authorization",
          "            data = open(resource, 'rb').read()")],
        "test_c22_effect_uses_the_bound_handle",
    ),
}

DRIVERS = (
    "tests/adversarial/test_g10_oracle_teeth.py",
    "tests/test_aep_mutants.py",
    "tests/test_envelope_mutants.py",
    "tests/test_correspondence_mutants.py",
    "tests/test_skill_contract_mutants.py",
    "tests/test_minimal_change_pilot.py",
)


def _copy_project(dst):
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".git", ".venv", ".venv-win")
    shutil.copytree(ROOT / "governance", dst / "governance", ignore=ignore)
    shutil.copytree(ROOT / "audit", dst / "audit", ignore=ignore)
    shutil.copytree(ROOT / "evidence", dst / "evidence", ignore=ignore)
    shutil.copytree(ROOT / "docs" / "evidence", dst / "docs" / "evidence", ignore=ignore)
    shutil.copytree(ROOT / "claims", dst / "claims", ignore=ignore)
    shutil.copytree(ROOT / "assurance", dst / "assurance", ignore=ignore)
    shutil.copytree(ROOT / "skills", dst / "skills", ignore=ignore)
    shutil.copytree(ROOT / "skilleval", dst / "skilleval", ignore=ignore)
    shutil.copytree(ROOT / "cognitive", dst / "cognitive", ignore=ignore)
    shutil.copytree(ROOT / "application", dst / "application", ignore=ignore)
    shutil.copytree(ROOT / "scripts", dst / "scripts", ignore=ignore)
    shutil.copytree(ROOT / "tests", dst / "tests", ignore=ignore)
    shutil.copy(ROOT / "pyproject.toml", dst / "pyproject.toml")
    for rel in DRIVERS:
        driver = dst / rel
        if driver.exists():
            driver.unlink()  # mutant drivers never run inside a mutant copy


def _pytest(cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )


def test_envelope_pristine_copy_is_green(tmp_path):
    _copy_project(tmp_path)
    r = _pytest(tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_envelope_mutant_is_killed_by_its_named_test(tmp_path, name):
    edits, relevant = MUTANTS[name]
    _copy_project(tmp_path)
    for rel, old, new in edits:
        target = tmp_path / rel
        src = target.read_text()
        assert old in src, "mutation site not found: %s in %s" % (name, rel)
        target.write_text(src.replace(old, new, 1))
    r = _pytest(tmp_path)
    assert r.returncode != 0, "mutant %s SURVIVED" % name
    failed = re.findall(r"FAILED \S+::(\w+)", r.stdout)
    assert relevant in failed, "%s killed, but not by %s (failed: %s)" % (name, relevant, failed)
    for rel, old, new in edits:  # the real tree still holds the original code
        assert old in (ROOT / rel).read_text()
