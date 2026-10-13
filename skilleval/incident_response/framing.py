"""Build the frozen prompt set for the epistemic-framing follow-up.

This module does not call a model or judge responses. It only makes the
controlled inputs auditable and deterministic.
"""
import hashlib
import json
from pathlib import Path

from skills import SkillDefinition
from skills.context import render_skill_context


DIR = Path(__file__).resolve().parent
ROOT = DIR.parents[1]
RUNS = DIR / "runs.json"
SKILL = ROOT / "skills" / "definitions" / "incident-response-commander.json"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _prompt(meta, briefing, skill_block):
    return meta["responder_template"].format(skill=skill_block, briefing=briefing, out="{out}")


def build_inputs():
    """Return the 20 frozen prompts, with the new framing in treatment only."""
    meta = _load(RUNS)["meta"]
    skill = SkillDefinition.from_dict(_load(SKILL))
    scenarios = {
        p.stem: _load(p) for p in sorted((DIR / "scenarios").glob("*.json"))
    }
    framed = meta["skill_block"].format(ctx=render_skill_context(skill))
    out = []
    for scenario, data in scenarios.items():
        briefing = data["briefing"]
        out.append({"scenario": scenario, "arm": "control", "prompt": _prompt(meta, briefing, "")})
        out.append({"scenario": scenario, "arm": "treatment", "prompt": _prompt(meta, briefing, framed)})
    return out


def prompt_set_sha256():
    payload = json.dumps(build_inputs(), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
