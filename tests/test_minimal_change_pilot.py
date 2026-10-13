"""Minimal Change Engineer: extraction fidelity + evaluation-harness fixture.

STATE: MINIMAL_CHANGE_EXTRACTION=PASS, MINIMAL_CHANGE_EVAL_HARNESS=PASS,
MINIMAL_CHANGE_CAPABILITY_ACQUISITION_TEST=NOT_VALID_AS_DESIGNED (baseline already had the behavior),
SKILL_TASK_CAPABILITY_VALIDATION=PENDING. The recorded with/without numbers are a HISTORICAL regression record of the
harness (they measure model discipline), not evidence that the Skill is effective.

Extraction fidelity : SOURCE_PINNED, PROVENANCE_EXACT, ADAPTATION_TRACEABLE_TO_SOURCE,
                      NO_TOOL_AUTHORITY_IMPORTED, NO_RUNTIME_SEMANTICS_IMPORTED   (SOURCE_TEXT != INSTALLED_SKILL)
Measured value      : scorer teeth, pre-registered decision rule, recorded runs re-scored from their stored final states.

KNOWN LIMITS: lint-level checks on the adapted text; the experiment is small (12 tasks, 1 sample per arm, one model family).
"""
import ast
import hashlib
import json
import pathlib

import pytest

from skilleval import gate, pilot, scoring
from skills import SkillDefinition, SkillProvenance
from skills.context import render_legacy_skill_context, render_skill_context

ROOT = pathlib.Path(__file__).resolve().parents[1]
REV = "83294689da3832c0a9f223221148c411fd3eacc0"
UPSTREAM_PATH = "engineering/engineering-minimal-change-engineer.md"
BLOB = "11d7660010fb70c5fcd4c36e2239afe357ecd335"
SRC_DIR = ROOT / "skills" / "sources" / "agency-agents" / REV
DEF = ROOT / "skills" / "definitions" / "minimal-change-engineer.json"
TRACE = ROOT / "skills" / "definitions" / "minimal-change-engineer.trace.json"
LISTS = ("critical_rules", "workflow", "deliverables", "evaluation_criteria")


def _source_bytes():
    return (SRC_DIR / "engineering-minimal-change-engineer.md").read_bytes().replace(b"\r\n", b"\n")


def _source():
    return _source_bytes().decode("utf-8")


def _def_dict():
    return json.loads(DEF.read_text(encoding="utf-8"))


def _skill():
    return SkillDefinition.from_dict(_def_dict())


def _trace():
    return json.loads(TRACE.read_text(encoding="utf-8"))


# ---- SOURCE_PINNED / PROVENANCE_EXACT -------------------------------------------------------------

def test_source_snapshot_matches_the_pinned_git_blob():
    data = _source_bytes()
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == BLOB


def test_license_and_notice_travel_with_the_adaptation():
    lic = (SRC_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in lic and "Copyright" in lic
    notice = (SRC_DIR / "NOTICE.md").read_text(encoding="utf-8")
    assert REV in notice and BLOB in notice and "SOURCE_TEXT != INSTALLED_SKILL" in notice


def test_provenance_points_at_the_exact_upstream_revision_not_a_branch():
    p = _skill().provenance
    assert p == SkillProvenance("msitarzewski/agency-agents", UPSTREAM_PATH, REV)
    assert len(p.source_revision) == 40 and all(c in "0123456789abcdef" for c in p.source_revision)
    up = _trace()["upstream"]
    assert (up["repo"], up["revision"], up["path"], up["blob"], up["license"]) == (
        p.source, REV, UPSTREAM_PATH, BLOB, "MIT")


def test_snapshot_directory_is_named_by_the_pinned_revision():
    assert SRC_DIR.name == _skill().provenance.source_revision


# ---- ADAPTATION_TRACEABLE_TO_SOURCE ---------------------------------------------------------------

def test_every_adapted_item_has_anchors_present_verbatim_in_the_source():
    trace, source, skill = _trace(), _source(), _skill()
    assert trace["mission"] and all(a in source for a in trace["mission"])
    for name in LISTS:
        items, groups = getattr(skill, name), trace[name]
        assert len(items) == len(groups), name
        for group in groups:
            assert group and all(a in source for a in group), (name, group)


def test_trace_has_no_entries_beyond_the_definition():
    assert set(_trace()) == {"upstream", "dropped_from_source", "mission", *LISTS}


def test_adaptation_is_a_small_rewrite_not_a_copy():
    adapted = json.dumps(_def_dict(), ensure_ascii=True)
    assert len(adapted) < len(_source())
    for line in _source().splitlines():
        if len(line.strip()) >= 60:
            assert line.strip() not in adapted, line


# ---- NO_TOOL_AUTHORITY / NO_RUNTIME_SEMANTICS (lint of the adapted text) ----------------------------

def _all_text():
    d = _def_dict()
    parts = [d[k] for k in ("name", "domain", "description", "mission")]
    for name in LISTS:
        parts += d[name]
    return "\n".join(parts)


def test_adapted_text_is_plain_ascii_without_persona_or_decoration():
    text = _all_text()
    assert text.isascii()
    low = text.lower()
    for word in ("emoji", "vibe", "personality", "memory", "you are", "your identity", "remember"):
        assert word not in low, word


def test_adapted_text_grants_no_tool_authority_or_runtime_behavior():
    low = _all_text().lower()
    for word in ("tool", "permission", "capabilit", "credential", "authoriz", "execute", "install", "invoke",
                 "http", "pull request", "issue #", "file an issue", "run the tests", "commit", "deploy"):
        assert word not in low, word


def test_definition_is_a_valid_closed_skill_with_no_authority_fields():
    d = _def_dict()
    assert set(d) == {"schema_version", "skill_id", "name", "domain", "description", "mission", *LISTS, "provenance"}
    assert SkillDefinition.from_dict(d).to_dict() == d


def test_skill_context_is_deterministic_and_carries_cognition_text_only():
    s = _skill()
    a, b = render_skill_context(s), render_skill_context(s)
    assert a == b
    for rule in s.critical_rules:
        assert rule in a
    assert s.provenance.source_revision not in a and "msitarzewski" not in a
    with pytest.raises(TypeError):
        render_skill_context(_def_dict())


# ---- import boundaries ----------------------------------------------------------------------------

def _imports(path):
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0] if node.level == 0 else "." * node.level + (node.module or ""))
            roots.update(a.name for a in node.names if node.level)
    return roots


def test_skilleval_imports_no_audit_evidence_claims_or_assurance():
    for path in sorted((ROOT / "skilleval").glob("*.py")):
        extra = _imports(path) & {"audit", "evidence", "claims", "assurance"}
        assert not extra, (path.name, extra)


@pytest.mark.parametrize("pkg", ["governance", "audit", "evidence", "claims", "assurance", "skills"])
def test_nothing_else_imports_skilleval(pkg):
    for path in sorted((ROOT / pkg).rglob("*.py")):
        assert "skilleval" not in _imports(path), path


# ---- tasks validate the scorer (baseline and reference) -------------------------------------------

TASKS = pilot.load_tasks()


def test_there_are_twelve_distinct_tasks():
    assert len(TASKS) == 12


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_task_baseline_and_reference_behave_as_designed(task_id):
    t = TASKS[task_id]
    base = scoring.score_run(t, dict(t["files"]))
    assert base.requirements_passed == 0 and base.regressions == 0 and base.scope_creep_events == 0
    ref = scoring.score_run(t, {**t["files"], **t["reference_files"]})
    assert ref.satisfied and ref.regressions == 0 and ref.scope_creep_events == 0 and ref.files_touched == 1


# ---- scorer teeth ---------------------------------------------------------------------------------

T05 = TASKS["t05-average-empty"]


def _t05(**edits):
    files = {**T05["files"], **T05["reference_files"]}
    files.update(edits)
    return files


def test_scorer_flags_an_unrequested_edit_of_neighbouring_code():
    stats = _t05()["stats.py"].replace("    n = len(s)\n", "    n = len(s)  # element count\n")
    s = scoring.score_run(T05, _t05(**{"stats.py": stats}))
    assert s.satisfied and s.scope_creep_events == 1 and s.unrequested_refactors == 1


def test_scorer_flags_a_behavior_change_outside_scope_as_a_regression_too():
    stats = _t05()["stats.py"].replace("def median(values):\n", "def median(values):\n    if not values:\n        return 0.0\n")
    s = scoring.score_run(T05, _t05(**{"stats.py": stats}))
    assert s.satisfied and s.regressions == 1 and s.unrequested_refactors == 1


def test_scorer_flags_new_files_and_new_symbols_as_additions():
    s = scoring.score_run(T05, _t05(**{"helpers.py": "X = 1\n"}))
    assert s.unrequested_additions == 1 and s.files_touched == 2
    stats = _t05()["stats.py"] + "\n\ndef variance(values):\n    return 0\n"
    s2 = scoring.score_run(T05, _t05(**{"stats.py": stats}))
    assert s2.unrequested_additions == 1 and s2.unrequested_refactors == 0


def test_scorer_detects_an_unsatisfied_requirement():
    s = scoring.score_run(T05, dict(T05["files"]))
    assert not s.satisfied and s.requirements_passed == 0


def test_scorer_detects_a_broken_module_as_regressions_and_not_satisfied():
    s = scoring.score_run(T05, _t05(**{"stats.py": "def average(:\n"}))
    assert not s.satisfied and s.regressions >= 3


def test_scorer_restores_visible_tests_so_they_cannot_be_weakened():
    stats = _t05()["stats.py"].replace("return sum(values) / len(values)", "return 0")
    weak = "def test_average():\n    assert True\n"
    s = scoring.score_run(T05, _t05(**{"stats.py": stats, "tests/test_stats.py": weak}))
    assert s.regressions >= 1


def test_blank_line_only_changes_do_not_create_scope_events():
    stats = _t05()["stats.py"].replace("\n\n\ndef median", "\n\n\n\ndef median")
    s = scoring.score_run(T05, _t05(**{"stats.py": stats}))
    assert s.scope_creep_events == 0


# ---- pre-registered decision rule -----------------------------------------------------------------

def _score(task_id, passed=2, total=2, reg=0, lines=2, creep=0):
    return {"task_id": task_id, "requirements_total": total, "requirements_passed": passed,
            "regressions": reg, "files_touched": 1, "lines_changed": lines, "scope_creep_events": creep,
            "unrequested_refactors": creep, "unrequested_additions": 0, "satisfied": passed == total}


def _arm(**kw):
    return [_score(f"t{i:02d}", **kw) for i in range(12)]


def _arm_with(first, rest=None):
    rest = rest or {}
    return [_score("t00", **first)] + [_score(f"t{i:02d}", **rest) for i in range(1, 12)]


def test_rule_pass_when_correctness_holds_and_scope_creep_and_diff_shrink():
    r = gate.decide(_arm_with({"creep": 2, "lines": 9}), _arm())
    assert r["verdict"] == "PASS"


def test_rule_fail_when_the_diff_shrinks_but_correctness_drops():
    r = gate.decide(_arm_with({"creep": 2, "lines": 9}), _arm_with({"passed": 1, "lines": 1}))
    assert r["verdict"] == "FAIL" and "fewer tasks" in r["reasons"][0]


def test_rule_fail_when_regressions_increase():
    r = gate.decide(_arm_with({"creep": 2, "lines": 9}), _arm_with({"reg": 1}))
    assert r["verdict"] == "FAIL" and "regressions" in r["reasons"][0]


def test_rule_inconclusive_without_headroom():
    assert gate.decide(_arm(), _arm())["verdict"] == "INCONCLUSIVE"


def test_rule_fail_when_scope_creep_is_not_reduced():
    r = gate.decide(_arm_with({"creep": 1}), _arm_with({"creep": 1}))
    assert r["verdict"] == "FAIL"


def test_rule_fail_when_the_overall_diff_grows():
    r = gate.decide(_arm_with({"creep": 2, "lines": 3}), _arm_with({"lines": 30}))
    assert r["verdict"] == "FAIL" and "diff" in r["reasons"][0]


def test_rule_inconclusive_for_too_few_tasks_or_mismatched_arms():
    few = [_score(f"t{i}") for i in range(3)]
    assert gate.decide(few, few)["verdict"] == "INCONCLUSIVE"
    other = [_score(f"x{i:02d}") for i in range(12)]
    assert gate.decide(_arm(), other)["verdict"] == "INCONCLUSIVE"


# ---- recorded pilot -------------------------------------------------------------------------------

RUNS = pilot.load_runs()
RESULTS = json.loads((pilot.PILOT_DIR / "results.json").read_text(encoding="utf-8"))


def test_recorded_runs_cover_every_task_exactly_once_per_arm():
    seen = {}
    for run in RUNS["runs"]:
        seen.setdefault(run["arm"], []).append(run["task_id"])
    assert set(seen) == {"control", "treatment"}
    for arm, ids in seen.items():
        assert sorted(ids) == sorted(TASKS), arm
    assert len({r["run_id"] for r in RUNS["runs"]}) == 24


def test_recorded_treatment_context_is_the_pinned_skill_context():
    assert RUNS["meta"]["skill_context_sha256"] == hashlib.sha256(render_legacy_skill_context(_skill()).encode()).hexdigest()
    assert RUNS["meta"]["skill_id"] == _skill().skill_id


def test_recorded_results_are_reproducible_from_the_stored_final_states():
    again = pilot.build_results(RUNS, TASKS)
    assert again["scores"] == RESULTS["scores"]
    assert again["decision"] == RESULTS["decision"]


def test_recorded_historical_numbers_are_kept_but_not_claimed_as_capability_evidence():
    d, meta = RESULTS["decision"], RESULTS["meta"]
    assert d["verdict"] == meta["historical_rule_verdict"] == "PASS"
    assert d["treatment"]["tasks_satisfied"] >= d["control"]["tasks_satisfied"]
    assert d["treatment"]["regressions"] <= d["control"]["regressions"]
    assert meta["evidence_strength"] == "LOW"
    assert meta["status"] == {
        "MINIMAL_CHANGE_EXTRACTION": "PASS",
        "MINIMAL_CHANGE_EVAL_HARNESS": "PASS",
        "MINIMAL_CHANGE_CAPABILITY_ACQUISITION_TEST": "NOT_VALID_AS_DESIGNED",
        "SKILL_TASK_CAPABILITY_VALIDATION": "PENDING",
    }
    assert "NOT capability acquisition" in meta["measured"] and "not an efficacy gate" in meta["use"]
    assert RUNS["meta"]["limits"]
