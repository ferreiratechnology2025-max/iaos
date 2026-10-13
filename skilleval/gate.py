"""Pre-registered decision rule for IAOS_MINIMAL_CHANGE_SKILL_PILOT_GATE (value gate).

Priority (written BEFORE any run; correctness outranks diff size):
    1. requirements satisfied  : treatment must not satisfy fewer tasks than control      else FAIL
    2. regressions             : treatment must not have more regressions                  else FAIL
    3. headroom                : control must show scope creep, otherwise nothing to reduce -> INCONCLUSIVE
    4. scope creep             : treatment must have strictly fewer events                 else FAIL
    5. diff size               : treatment total lines_changed must not exceed control     else FAIL
"smallest diff at any cost" is never a pass: (1) and (2) are checked first.
"""
from typing import Dict, Iterable, List

MIN_TASKS = 10
KEYS = ("requirements_passed", "requirements_total", "regressions", "files_touched",
        "lines_changed", "scope_creep_events", "unrequested_refactors", "unrequested_additions")


def aggregate(scores: Iterable[dict]) -> Dict[str, int]:
    scores = list(scores)
    out = {k: sum(int(s[k]) for s in scores) for k in KEYS}
    out["tasks"] = len(scores)
    out["tasks_satisfied"] = sum(1 for s in scores if s["satisfied"])
    return out


def decide(control: List[dict], treatment: List[dict], min_tasks: int = MIN_TASKS) -> Dict[str, object]:
    ids_c = sorted(s["task_id"] for s in control)
    ids_t = sorted(s["task_id"] for s in treatment)
    c, t = aggregate(control), aggregate(treatment)
    reasons: List[str] = []
    if ids_c != ids_t or len(ids_c) != len(set(ids_c)):
        verdict = "INCONCLUSIVE"
        reasons.append("arms do not cover the same set of distinct tasks")
    elif len(ids_c) < min_tasks:
        verdict = "INCONCLUSIVE"
        reasons.append(f"fewer than {min_tasks} tasks")
    elif t["tasks_satisfied"] < c["tasks_satisfied"]:
        verdict = "FAIL"
        reasons.append("treatment satisfies fewer tasks than control")
    elif t["regressions"] > c["regressions"]:
        verdict = "FAIL"
        reasons.append("treatment has more regressions than control")
    elif c["scope_creep_events"] == 0:
        verdict = "INCONCLUSIVE"
        reasons.append("control shows no scope creep: no headroom to measure")
    elif t["scope_creep_events"] >= c["scope_creep_events"]:
        verdict = "FAIL"
        reasons.append("scope creep not reduced")
    elif t["lines_changed"] > c["lines_changed"]:
        verdict = "FAIL"
        reasons.append("diff not smaller overall")
    else:
        verdict = "PASS"
        reasons.append("correctness and regressions not worse; scope creep and diff reduced")
    return {"verdict": verdict, "reasons": reasons, "control": c, "treatment": t}
