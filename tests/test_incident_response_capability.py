"""IAOS_INCIDENT_RESPONSE_SKILL_CAPABILITY_GATE: extraction fidelity, evaluator teeth, neutral contract, recorded result.

The recorded verdict is whatever the pre-registered rule says (currently FAIL: treatment invents more facts). These tests
check that the evidence is reproducible and the rule is applied as written; they do not assert efficacy.
KNOWN LIMITS: 10 scenarios, 1 sample per cell, model judges from the responder's family, synthetic briefings.
"""
import hashlib
import json
import pathlib
import re

import pytest

from skilleval import rubric as R
from skilleval.incident_response.framing import build_inputs, prompt_set_sha256
from skills import SkillDefinition
from skills.context import render_legacy_skill_context, render_skill_context

ROOT = pathlib.Path(__file__).resolve().parents[1]
REV = "83294689da3832c0a9f223221148c411fd3eacc0"
BLOB = "a81d24957215b077a655111936ed4ff6a461d92d"
SRC = ROOT / "skills/sources/agency-agents" / REV / "engineering-incident-response-commander.md"
DEF = ROOT / "skills/definitions/incident-response-commander.json"
TRACE = ROOT / "skills/definitions/incident-response-commander.trace.json"
LISTS = ("critical_rules", "workflow", "deliverables", "evaluation_criteria")


def _d():
    return json.loads(DEF.read_text(encoding="utf-8"))


# ---- extraction fidelity ---------------------------------------------------------------------------

def test_source_snapshot_matches_the_pinned_git_blob():
    data = SRC.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == BLOB


def test_provenance_is_exact():
    p = _d()["provenance"]
    assert p == {"source": "msitarzewski/agency-agents",
                 "source_path": "engineering/engineering-incident-response-commander.md", "source_revision": REV}


def test_every_adapted_item_has_anchors_present_verbatim_in_the_source():
    src = SRC.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    trace, d = json.loads(TRACE.read_text(encoding="utf-8")), _d()
    n = 0
    for key in LISTS:
        for entry in trace[key]:
            anchors = entry["anchors"] if isinstance(entry, dict) else entry
            assert anchors
            for a in anchors:
                assert a in src, (key, a)
                n += 1
    assert n >= len(d["critical_rules"]) + len(d["workflow"])


def test_definition_is_a_valid_closed_skill_and_grants_no_authority():
    skill = SkillDefinition.from_dict(_d())
    text = render_skill_context(skill).lower()
    for word in ("tool", "permission", "credential", "capabilit", "hook", "authority to", "you are authorized", "execute"):
        assert word not in text, word
    bad = dict(_d(), tools=["x"])
    with pytest.raises(Exception):
        SkillDefinition.from_dict(bad)


def test_skill_context_hash_matches_the_recorded_experiment():
    ctx = render_legacy_skill_context(SkillDefinition.from_dict(_d()))
    assert hashlib.sha256(ctx.encode("utf-8")).hexdigest() == R.load_runs()["meta"]["skill_context_sha256"]


def test_current_renderer_adds_only_generic_epistemic_framing_to_the_skill_body():
    skill = SkillDefinition.from_dict(_d())
    framed = render_skill_context(skill)
    legacy = render_legacy_skill_context(skill)
    assert framed.endswith(legacy)
    assert framed.removesuffix(legacy).startswith("The following Skill is procedural guidance")
    assert "NOT facts about the user's organization" in framed
    assert "Do not state or imply that the organization adopted these rules" in framed


def test_authorized_framing_input_set_has_one_control_and_one_treatment_per_scenario():
    inputs = build_inputs()
    scenarios = R.load_scenarios()
    assert len(inputs) == 2 * len(scenarios)
    assert {(x["scenario"], x["arm"]) for x in inputs} == {
        (scenario, arm) for scenario in scenarios for arm in R.ARMS
    }
    for scenario in scenarios:
        control = next(x for x in inputs if x["scenario"] == scenario and x["arm"] == "control")
        treatment = next(x for x in inputs if x["scenario"] == scenario and x["arm"] == "treatment")
        assert "The following Skill is procedural guidance" not in control["prompt"]
        assert "The following Skill is procedural guidance" in treatment["prompt"]
        assert control["prompt"].replace("{out}", "") != treatment["prompt"].replace("{out}", "")
    assert len(prompt_set_sha256()) == 64


# ---- neutral contract -----------------------------------------------------------------------------

def test_rubric_and_scenarios_require_no_literal_upstream_term():
    rub, sc = R.load_rubric(), R.load_scenarios()
    blob = json.dumps({"items": rub["items"], "scenarios": list(sc.values())})
    for rx in rub["banned_literals"]:
        assert not re.search(rx, blob, re.I if not rx.startswith("\\bSEV") else 0), rx


def test_there_are_enough_distinct_scenarios_and_each_has_a_safety_item():
    sc = R.load_scenarios()
    assert len(sc) >= R.MIN_SCENARIOS
    assert all(s["safety_item"] and s["briefing"] for s in sc.values())


def test_both_arms_share_one_template_and_five_neutral_sections():
    meta = R.load_runs()["meta"]
    for h in ("Situation", "Actions", "Decision rationale", "Communications", "Next steps"):
        assert h in meta["responder_template"]
    for banned in ("Severity", "Rollback", "Incident Commander", "post-mortem"):
        assert banned.lower() not in meta["responder_template"].lower()


def test_runs_cover_every_scenario_once_per_arm_with_two_judges():
    runs = R.load_runs()["runs"]
    sc = R.load_scenarios()
    for arm in R.ARMS:
        assert sorted(r["scenario"] for r in runs if r["arm"] == arm) == sorted(sc)
    assert all(len(r["judgements"]) == 2 for r in runs)
    assert len({r["run_id"] for r in runs}) == len(runs)


# ---- evaluator teeth ------------------------------------------------------------------------------

RESP = "We declare the incident now and name an owner for each stream of work."


def test_a_yes_needs_a_verbatim_quote_that_occurs_in_the_response():
    assert R.valid_yes(RESP, {"verdict": "yes", "quote": "name an owner for each stream"})
    assert not R.valid_yes(RESP, {"verdict": "yes", "quote": "assign roles to every responder"})
    assert not R.valid_yes(RESP, {"verdict": "yes", "quote": "owner"})  # too short
    assert not R.valid_yes(RESP, {"verdict": "no", "quote": "name an owner for each stream"})


def test_quote_matching_ignores_case_and_whitespace_only():
    assert R.quote_ok(RESP, "NAME  an owner\nfor each STREAM")


def test_item_passes_only_if_both_judges_give_a_valid_yes():
    rub, sc = R.load_rubric(), next(iter(R.load_scenarios().values()))
    yes = {"verdict": "yes", "quote": "name an owner for each stream"}
    ids = R.item_ids(rub)
    a = {"items": {i: yes for i in ids}, "invented": []}
    b = {"items": {i: dict(yes, verdict="no") for i in ids}, "invented": []}
    s = R.score_response(rub, sc, RESP, [a, b])
    assert not any(s["items"].values()) and s["agreement"] == 0.0
    s = R.score_response(rub, sc, RESP, [a, a])
    assert all(s["items"].values()) and s["agreement"] == 1.0


def test_invented_facts_count_only_if_in_response_and_absent_from_briefing():
    brief = "Errors rose at 10:00 after the deploy of the checkout service."
    resp = "Errors rose at 10:00 after the deploy of the checkout service. The vendor confirmed a regional outage."
    j = {"invented": [{"quote": "The vendor confirmed a regional outage."},
                      {"quote": "Errors rose at 10:00 after the deploy"},
                      {"quote": "something the response never says at all"}]}
    assert R.invented_count(resp, brief, j) == 1


# ---- pre-registered decision rule truth table -----------------------------------------------------

def _s(i, safety=1, procedure=0, ordering=0, concrete=0, invented=0.0, agreement=1.0):
    return {"scenario": f"s{i:02d}", "safety": safety, "procedure": procedure, "ordering": ordering,
            "concrete": concrete, "invented": invented, "agreement": agreement}


def _arm(n=10, **kw):
    return [_s(i, **kw) for i in range(n)]


def test_rule_pass_when_better_everywhere_and_nothing_worse():
    assert R.decide_capability(_arm(procedure=1), _arm(procedure=5, ordering=2, concrete=2))["verdict"] == "PASS"


def test_rule_fail_when_treatment_invents_more_facts():
    t = _arm(procedure=5, ordering=2, concrete=2)
    t[0]["invented"] = 1
    assert R.decide_capability(_arm(procedure=1), t)["verdict"] == "FAIL"


def test_rule_fail_when_treatment_is_less_safe():
    t = _arm(procedure=5, ordering=2, concrete=2)
    t[0]["safety"] = 0
    assert R.decide_capability(_arm(procedure=1), t)["verdict"] == "FAIL"


def test_rule_fail_below_margin():
    assert R.decide_capability(_arm(procedure=3), _arm(procedure=3, ordering=0))["verdict"] == "FAIL"


def test_rule_inconclusive_cases():
    assert R.decide_capability(_arm(5), _arm(5))["verdict"] == "INCONCLUSIVE"  # too few scenarios
    assert R.decide_capability(_arm(procedure=5, ordering=2, concrete=2), _arm(procedure=5, ordering=2, concrete=2))["verdict"] == "INCONCLUSIVE"  # ceiling
    low = _arm(procedure=1)
    low[0]["agreement"] = 0.0
    low = [dict(s, agreement=0.5) for s in low]
    assert R.decide_capability(low, _arm(procedure=5, ordering=2, concrete=2))["verdict"] == "INCONCLUSIVE"  # judges disagree
    assert R.decide_capability(_arm(procedure=1), _arm(9, procedure=5))["verdict"] == "INCONCLUSIVE"  # arms differ


# ---- recorded result is reproducible --------------------------------------------------------------

def test_recorded_results_are_reproduced_from_stored_responses_and_judgements():
    stored = json.loads((R.DIR / "results.json").read_text(encoding="utf-8"))
    assert R.rescore(R.load_runs()) == stored


def test_recorded_verdict_is_what_the_pre_registered_rule_gives_and_is_not_overridden():
    d = json.loads((R.DIR / "results.json").read_text(encoding="utf-8"))["decision"]
    assert d["verdict"] == "FAIL" and d["reasons"] == ["treatment invents more facts"]
    assert d["mean_judge_agreement"] >= R.MIN_AGREEMENT
