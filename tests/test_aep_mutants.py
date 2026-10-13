"""AEP-gate mutants, run on an isolated copy of the project.

For each mutant: the pristine copy is GREEN, the mutated copy is RED, and a
*relevant* test is among the failures. The real tree is never modified.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

ADAPTER = "governance/aep_adapter.py"
RUNTIME = "governance/aep_runtime.py"
EXECUTORS = "governance/aep_executors.py"
HANDLE = "governance/handle_binding.py"

MUTANTS = {
    "M1_auth_bypass": (
        [
            (ADAPTER,
             '        if not isinstance(action, AuthorizedAction):\n            raise TypeError("adapter accepts only AuthorizedAction")',
             '        if False:\n            raise TypeError("adapter accepts only AuthorizedAction")'),
        ],
        "test_a1_no_authorization_reaches_no_executor",
    ),
    "M2_registry_fallback": (
        [(EXECUTORS,
          "            raise UnknownExecutor(action)\n        return entry",
          "            return next(iter(self._entries.values()))\n        return entry")],
        "test_a6_unknown_registry_entry_fails_before_exec_without_fallback",
    ),
    "M3_resource_substitution": (
        [(RUNTIME,
          "                    raw = executor.run(record.resource, args, record.resource_identity, binding)",
          '                    raw = executor.run(record.resource[:-5] + "b.txt", args, record.resource_identity, binding)')],
        "test_a3_resource_substitution_cannot_reach_another_file",
    ),
    "M4_replay": (
        [("governance/consumption.py",
          "        return ConsumeResult.ALREADY_CONSUMED",
          "        return ConsumeResult.CONSUMED")],
        "test_a5_replay_second_exec_denied_total_exec_one",
    ),
    "M_RESOURCE_REOPEN_BY_PATH": (
        # EXEC goes back to opening/reading the PATH instead of the bound handle
        [(EXECUTORS,
          "            data = binding.read_all()  # the SAME handle opened during authorization",
          "            data = open(resource, 'rb').read()")],
        "test_binding_post_auth_swap_file_to_symlink_reads_original_not_secret",
    ),
    "M_AUTH_RESOLVES_BY_PATH": (
        # authorization judges the PATH STRING again instead of the open object's final path
        [(HANDLE,
          "            final_path = final_path_of_fd(fd)",
          "            final_path = os.path.realpath(path)")],
        "test_auth_flipflop_symlink_to_outside_during_open_is_denied_and_reads_nothing",
    ),
    "M_EXEC_IDENTITY_CHECK_REMOVED": (
        [(EXECUTORS,
          "            if executed != identity:\n                raise ExecutorError(\"resource_identity_mismatch\")",
          "            if False:\n                raise ExecutorError(\"resource_identity_mismatch\")")],
        "test_binding_executor_refuses_a_binding_whose_identity_is_not_the_authorized_one",
    ),
    "M_HANDLE_NOT_RELEASED_ON_DENY": (
        [("governance/authorization.py",
          "        if outcome.authorized_action is None:  # not ALLOW: nothing may stay open\n            self._canon.release(canon.resource_binding_id)",
          "        if outcome.authorized_action is None:  # not ALLOW: nothing may stay open\n            pass")],
        "test_auth_non_allow_outcomes_release_the_handle",
    ),
    "M5_args_validation_removed": (
        [(RUNTIME,
          '        if tuple(k for k, _ in env.authorized_args) != executor.arg_names:\n            raise EnvelopeRefused("args_shape")',
          '        if False:\n            raise EnvelopeRefused("args_shape")')],
        "test_a4_extra_authority_arg_fails_closed_before_boot",
    ),
}


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
    for driver in (
        dst / "tests" / "adversarial" / "test_g10_oracle_teeth.py",
        dst / "tests" / "test_aep_mutants.py",
        dst / "tests" / "test_envelope_mutants.py",
        dst / "tests" / "test_correspondence_mutants.py",
        dst / "tests" / "test_skill_contract_mutants.py",
        dst / "tests" / "test_minimal_change_pilot.py",
    ):
        if driver.exists():
            driver.unlink()


def _pytest(cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )


def test_pristine_copy_is_green(tmp_path):
    _copy_project(tmp_path)
    r = _pytest(tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_aep_mutant_is_killed_by_a_relevant_test(tmp_path, name):
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
