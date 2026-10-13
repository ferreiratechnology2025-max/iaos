"""Skill capability evaluation: wording-agnostic rubric, evidence-checked judgements, pre-registered decision rule.

Question this answers (SKILL_TASK_CAPABILITY): given a Skill, does the SAME model perform a procedure more reliably,
and produce that procedure's deliverables, than the model improvising from general knowledge? CONTROL may know the
procedure from pretraining; that is allowed. Nothing here grants authority: responses are text produced by cognition.

How a verdict is made deterministic enough to audit:
  * the rubric items are generic and wording-agnostic (no literal term of the source is required);
  * two blind judges answer every item; a "yes" counts ONLY with a verbatim quote that really occurs in the response;
  * an item passes only if BOTH judges give a valid "yes"; judge agreement is measured;
  * "invented facts" are quotes that occur in the response and do not occur in the briefing.
"""
import json
import re
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
DIR = HERE / "incident_response"

MIN_QUOTE = 12
MIN_SCENARIOS = 8
MIN_AGREEMENT = 0.80
CEILING = 0.95
MARGIN = 0.10
MIN_NOT_WORSE = 0.70
MIN_BETTER = 0.50
CATEGORIES = ("safety", "procedure", "ordering", "concrete")


def load_rubric() -> dict:
    return json.loads((DIR / "rubric.json").read_text(encoding="utf-8"))


def load_scenarios() -> Dict[str, dict]:
    out = {}
    for path in sorted((DIR / "scenarios").glob("*.json")):
        s = json.loads(path.read_text(encoding="utf-8"))
        out[s["id"]] = s
    return out


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def quote_ok(response: str, quote) -> bool:
    return isinstance(quote, str) and len(norm(quote)) >= MIN_QUOTE and norm(quote) in norm(response)


def valid_yes(response: str, entry) -> bool:
    return isinstance(entry, dict) and entry.get("verdict") == "yes" and quote_ok(response, entry.get("quote"))


def item_ids(rubric: dict) -> List[str]:
    return [i["id"] for i in rubric["items"]]


def invented_count(response: str, briefing: str, judgement: dict) -> int:
    seen = set()
    for entry in judgement.get("invented") or []:
        q = entry.get("quote") if isinstance(entry, dict) else None
        if quote_ok(response, q) and norm(q) not in norm(briefing):
            seen.add(norm(q))
    return len(seen)


def score_response(rubric: dict, scenario: dict, response: str, judgements: List[dict]) -> dict:
    """judgements: exactly two judge records {"judge","items":{id:{verdict,quote}},"invented":[{quote}]}."""
    assert len(judgements) == 2
    ids = item_ids(rubric)
    passed, agree = {}, 0
    for i in ids:
        a, b = (valid_yes(response, j.get("items", {}).get(i)) for j in judgements)
        passed[i] = a and b
        agree += int(a == b)
    cat = {c: 0 for c in CATEGORIES}
    for item in rubric["items"]:
        cat[item["category"]] += int(passed[item["id"]])
    invented = sum(invented_count(response, scenario["briefing"], j) for j in judgements) / 2
    return {"scenario": scenario["id"], "items": passed, **cat, "invented": invented, "agreement": agree / len(ids)}


# ---- pre-registered decision rule -----------------------------------------------------------------

def _tot(scores, key):
    return sum(s[key] for s in scores)


def _total(s):
    return sum(s[c] for c in CATEGORIES)


def decide_capability(control: List[dict], treatment: List[dict], max_total: int = 10) -> dict:
    """Priority: safe handling > required steps > ordering > concrete criteria > no invented facts; then margin.

    INCONCLUSIVE: arms differ / too few scenarios / judges disagree too much / control already at the ceiling.
    FAIL        : treatment worse on safety, ordering, concrete criteria or invented facts; or improvement below margin.
    PASS        : none of the above, improvement >= MARGIN overall, better-or-equal in >= 70% and better in >= 50% of scenarios.
    """
    ids_c = sorted(s["scenario"] for s in control)
    ids_t = sorted(s["scenario"] for s in treatment)
    n = len(ids_c)
    cn = max_total * n or 1
    detail = {
        "scenarios": n,
        "control": {**{c: _tot(control, c) for c in CATEGORIES}, "invented": _tot(control, "invented"),
                    "total": sum(_total(s) for s in control)},
        "treatment": {**{c: _tot(treatment, c) for c in CATEGORIES}, "invented": _tot(treatment, "invented"),
                      "total": sum(_total(s) for s in treatment)},
    }
    agreements = [s["agreement"] for s in control + treatment]
    detail["mean_judge_agreement"] = sum(agreements) / len(agreements) if agreements else 0.0
    reasons: List[str] = []
    c, t = detail["control"], detail["treatment"]
    if ids_c != ids_t or len(set(ids_c)) != n:
        verdict, why = "INCONCLUSIVE", "arms do not cover the same distinct scenarios"
    elif n < MIN_SCENARIOS:
        verdict, why = "INCONCLUSIVE", f"fewer than {MIN_SCENARIOS} scenarios"
    elif detail["mean_judge_agreement"] < MIN_AGREEMENT:
        verdict, why = "INCONCLUSIVE", "judges disagree too much"
    elif c["total"] / cn >= CEILING:
        verdict, why = "INCONCLUSIVE", "control already at the ceiling: no headroom"
    elif t["safety"] < c["safety"]:
        verdict, why = "FAIL", "treatment handles fewer scenarios safely"
    elif t["ordering"] < c["ordering"]:
        verdict, why = "FAIL", "treatment respects ordering constraints less often"
    elif t["concrete"] < c["concrete"]:
        verdict, why = "FAIL", "treatment states fewer concrete verifiable criteria"
    elif t["invented"] > c["invented"]:
        verdict, why = "FAIL", "treatment invents more facts"
    else:
        gain = (t["total"] - c["total"]) / cn
        cmap = {s["scenario"]: _total(s) for s in control}
        tmap = {s["scenario"]: _total(s) for s in treatment}
        not_worse = sum(1 for k in cmap if tmap[k] >= cmap[k]) / n
        better = sum(1 for k in cmap if tmap[k] > cmap[k]) / n
        detail.update({"gain": gain, "not_worse_share": not_worse, "better_share": better})
        if gain < MARGIN:
            verdict, why = "FAIL", "improvement below the pre-registered margin"
        elif not_worse < MIN_NOT_WORSE or better < MIN_BETTER:
            verdict, why = "FAIL", "improvement not consistent across scenarios"
        else:
            verdict, why = "PASS", "safe handling, ordering, concreteness and facts not worse; improvement above margin and consistent"
    reasons.append(why)
    return {"verdict": verdict, "reasons": reasons, **detail}


# ---- recorded experiment ---------------------------------------------------------------------------

ARMS = ("control", "treatment")


def load_runs() -> dict:
    return json.loads((DIR / "runs.json").read_text(encoding="utf-8"))


def rescore(runs: dict) -> dict:
    """Re-derive scores and the verdict from the recorded responses and judgements only."""
    rubric, scenarios = load_rubric(), load_scenarios()
    by: Dict[str, List[dict]] = {a: [] for a in ARMS}
    for run in runs["runs"]:
        by[run["arm"]].append(score_response(rubric, scenarios[run["scenario"]], run["response"], run["judgements"]))
    return {"scores": by, "decision": decide_capability(by["control"], by["treatment"])}
