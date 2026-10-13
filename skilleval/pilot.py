"""Minimal Change Engineer pilot: load the recorded runs, re-score them deterministically, apply the pre-registered rule.

Nothing here executes anything on behalf of a Skill; runs are recorded final repository states produced by a COGNITION-only
experiment (a model edits a throw-away copy of a fixture repo). The scorer and the rule live in scoring.py / gate.py.
"""
import hashlib
import json
from pathlib import Path
from typing import Dict, List

from . import gate, scoring

HERE = Path(__file__).resolve().parent
TASKS_DIR = HERE / "tasks"
PILOT_DIR = HERE / "pilot_minimal_change"
ARMS = ("control", "treatment")


def load_tasks() -> Dict[str, dict]:
    tasks = {}
    for path in sorted(TASKS_DIR.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        tasks[task["id"]] = task
    return tasks


def load_runs() -> dict:
    return json.loads((PILOT_DIR / "runs.json").read_text(encoding="utf-8"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rescore(runs: dict, tasks: Dict[str, dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {arm: [] for arm in ARMS}
    for run in runs["runs"]:
        score = scoring.score_run(tasks[run["task_id"]], run["files"]).to_dict()
        score["run_id"] = run["run_id"]
        out[run["arm"]].append(score)
    for arm in ARMS:
        out[arm].sort(key=lambda s: s["task_id"])
    return out


def build_results(runs: dict, tasks: Dict[str, dict]) -> dict:
    scores = rescore(runs, tasks)
    decision = gate.decide(scores["control"], scores["treatment"])
    return {"decision": decision, "scores": scores}
