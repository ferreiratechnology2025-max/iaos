"""Mutants for the Skill contract (IAOS_SKILL_CONTRACT_GATE).

Pristine copy GREEN on tests/test_skill_contract.py; each mutated copy RED and the NAMED test among the failures.

    M_SKILL_ACCEPTS_UNKNOWN_KEY     closed schema only requires the known keys to be present
    M_SKILL_MUTABLE                 SkillDefinition is no longer frozen
    M_SKILL_PROVENANCE_OPTIONAL     source_revision not validated
    M_SKILL_ALIASES_INPUT_LIST      list fields keep the caller's list instead of an owned tuple
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = "skills/contract.py"
TARGET = "tests/test_skill_contract.py"

MUTANTS = {
    "M_SKILL_ACCEPTS_UNKNOWN_KEY": (
        [(CONTRACT, "    if keys != expected:", "    if not keys >= expected:")],
        "test_unknown_key_is_rejected",
    ),
    "M_SKILL_MUTABLE": (
        [(CONTRACT, "@dataclass(frozen=True)\nclass SkillDefinition:", "@dataclass\nclass SkillDefinition:")],
        "test_skill_definition_is_frozen",
    ),
    "M_SKILL_PROVENANCE_OPTIONAL": (
        [(CONTRACT, '        _text("provenance.source_revision", self.source_revision)', "        pass")],
        "test_provenance_is_required_and_non_empty",
    ),
    "M_SKILL_ALIASES_INPUT_LIST": (
        [(CONTRACT, "    return tuple(out)", "    return value")],
        "test_input_list_mutation_does_not_change_definition",
    ),
}


def _copy_project(dst):
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".git", ".venv", ".venv-win")
    for pkg in ("skills", "governance", "audit", "evidence", "claims", "assurance", "tests"):
        shutil.copytree(ROOT / pkg, dst / pkg, ignore=ignore)
    shutil.copy(ROOT / "pyproject.toml", dst / "pyproject.toml")


def _pytest(cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TARGET],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )


def test_pristine_copy_is_green(tmp_path):
    _copy_project(tmp_path)
    r = _pytest(tmp_path)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_killed_by_its_named_test(name, tmp_path):
    edits, killer = MUTANTS[name]
    _copy_project(tmp_path)
    for rel, old, new in edits:
        path = tmp_path / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (name, rel, "mutation site not found exactly once")
        path.write_text(text.replace(old, new), encoding="utf-8")
    r = _pytest(tmp_path)
    assert r.returncode != 0, f"{name} SURVIVED"
    failed = set(re.findall(r"FAILED \S+::(\w+)", r.stdout))
    assert killer in failed, f"{name}: expected {killer} among {sorted(failed)}"
