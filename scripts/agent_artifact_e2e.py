"""IAOS_AGENT_LOOP_V1_2_ARTIFACT_GATE real harness (8B).

Usage (repo root):
  python -m scripts.agent_artifact_e2e --computer-env <file> --agnes-env <file>

The only agent input is the goal text. The harness does NOT close the "Choose Design" modal,
does not navigate, does not tell the agent where the composer is. allowed_origins and the
forbidden controls (Publish/Publicar/Upgrade) are harness-owned constraints, never model input.
If authentication reappears the person only uses Assumir / Devolver.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

from application.agent_runtime import build_agent_loop
from application.computer_runtime import OpenBotComputerAdapter
from application.daily_brief import configured_agnes_provider
from application.eightb_verifier import BUTTON, SUBTITLE, TITLE, summarize_eightb, verify_eightb_artifact
from cognitive.agent_contracts import AgentGoal
from scripts.agent_todomvc_e2e import AGNES_KEYS, COMPUTER_KEYS, read_env

ALLOWED = ("https://ai.8b.com",)
FORBIDDEN = ("Publish", "Publicar", "Upgrade")
GOAL = AgentGoal(
    goal_id="artifact-e2e",
    objective=(
        "Crie no 8B uma landing page simples de uma p\u00e1gina.\n\n"
        f"T\u00edtulo principal:\n{TITLE}\n\n"
        f"Subt\u00edtulo:\n{SUBTITLE}\n\n"
        f"Inclua um bot\u00e3o:\n{BUTTON}\n\n"
        "N\u00e3o publique."
    ),
    constraints=(),
    max_steps=60,
)


def yn(v):
    return "PASS" if v else "FAIL"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--computer-env", required=True)
    parser.add_argument("--agnes-env", required=True)
    parser.add_argument("--existing-project-id", default=None,
                        help="V1_2_EXISTING_PROJECT_MODE: harness SETUP opens this exact 8B project before the agent starts")
    args = parser.parse_args(argv)
    computer_cfg = read_env(args.computer_env, COMPUTER_KEYS)
    agnes_cfg = read_env(args.agnes_env, AGNES_KEYS)
    print(f"COMPUTER_CONFIG_COMPLETE={'YES' if len(computer_cfg) == len(COMPUTER_KEYS) else 'NO'}")
    print(f"AGNES_KEY_PRESENT={'YES' if 'AGNES_API_KEY' in agnes_cfg else 'NO'}")
    print("SECRET_VALUES_PRINTED=NO")
    if len(computer_cfg) != len(COMPUTER_KEYS) or "AGNES_API_KEY" not in agnes_cfg:
        return 2
    os.environ.update(agnes_cfg)
    port = OpenBotComputerAdapter(computer_cfg["IAOS_COMPUTER_URL"], computer_cfg["IAOS_COMPUTER_BOT_ID"],
                                  computer_cfg["IAOS_COMPUTER_TOKEN"])
    control = port.read_control_state(None)
    start = port.observe()
    print(f"PRECONDITION holder={control.holder} requested={control.requested} start_page={start.page_url}")
    existing = args.existing_project_id
    if existing is not None and not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", existing):
        print("PRECONDITION=FAIL (--existing-project-id must be an exact project UUID)")
        return 3
    if control.holder != "bot" or control.requested or (existing is None and start.page_url != "about:blank"):
        print("PRECONDITION=FAIL (need holder=bot, no pending request, INITIAL_PAGE=about:blank unless existing-project mode)")
        return 3
    started_at_blank = start.page_url == "about:blank"
    if existing is not None:
        # SETUP (harness, not an agent action): open the exact project, then verify the preconditions.
        final_url = port.navigate(f"https://ai.8b.com/projects/{existing}")
        time.sleep(8)
        setup = port.observe()
        control = port.read_control_state(None)
        setup_facts = verify_eightb_artifact(setup).facts
        path_ok = final_url.rstrip("/").endswith(f"/projects/{existing}") and setup.page_url.rstrip("/").endswith(f"/projects/{existing}")
        present = all(setup_facts.get(k) for k in ("TITLE", "SUBTITLE", "BUTTON"))
        editor_ok = setup_facts.get("PREVIEW_CANDIDATES") == 1
        print(f"EXISTING_PROJECT_PREBOUND={'YES' if path_ok else 'NO'} PROJECT_ID={existing}")
        print(f"EDITOR_ACCESSIBLE={'YES' if editor_ok else 'NO'}")
        print(f"8B_TARGET_ARTIFACT_ALREADY_PRESENT={'YES' if present else 'NO'}")
        print(f"SETUP holder={control.holder} pending_takeover={'YES' if control.requested else 'NO'}")
        if not (path_ok and editor_ok and not present and control.holder == "bot" and not control.requested):
            print("SETUP=FAIL (agent not started)")
            return 3

    records: list[dict] = []

    def summarize(obs):
        facts = verify_eightb_artifact(obs).facts
        records.append({"dialog": "Choose Design" in obs.aria_snapshot, "facts": facts})
        return summarize_eightb(obs)

    provider = configured_agnes_provider()
    loop, session = build_agent_loop(provider, port, verify_eightb_artifact, log=print, summarize=summarize,
                                     allowed_origins=ALLOWED, forbidden_controls=FORBIDDEN, poll_interval=2.0)
    result = loop.run(GOAL)
    log = result.log
    final_obs = port.observe()

    def idx(pred, start_at=0):
        return next((i for i in range(start_at, len(log)) if pred(log[i])), None)

    nav_ok = idx(lambda l: "navigation OK" in l) is not None
    type_i = idx(lambda l: "Agnes decided browser.type" in l and TITLE in l)
    submit_i = idx(lambda l: "Agnes decided browser.key" in l or "Agnes decided browser.click" in l, (type_i or 0) + 1) if type_i is not None else None
    submit_ok = submit_i is not None and idx(lambda l: "action OK" in l, submit_i) is not None
    shown_i = idx(lambda l: "OBSERVATION:" in l and "'TITLE': True" in l, submit_i or 0) if submit_i is not None else None
    window_end = shown_i if shown_i is not None else len(log)
    during_generation = sum(1 for l in log[(submit_i or 0) + 1:window_end] if "Agnes decided browser." in l or "Agnes decided NAVIGATE" in l) if submit_i is not None else None
    dialog_first = records[0]["dialog"] if records else False
    modal_resolved = dialog_first and any(not r["dialog"] for r in records[1:])
    boundary = result.boundaries[0] if result.boundaries else None
    publish_actions = sum(1 for c in result.clicks if re.search(r"\b(publish|publicar)\b", c, re.I))
    upgrade_actions = sum(1 for c in result.clicks if re.search(r"\bupgrade\b", c, re.I))
    facts = result.verification.facts if result.verification else {}
    verified = result.status == "VERIFIED"

    print("---")
    print(f"RUN_STATUS={result.status} turns={result.turns} waits={result.waits}")
    print("GOAL_ONLY_INPUT=YES")
    print("ALLOWED_ORIGINS_EXPOSED_TO_COGNITION=YES")
    print("ALLOWED_ORIGINS_SOURCE=TRUSTED_RUN_CONFIG")
    for line in log:
        if "NAVIGATE REQUESTED_ORIGIN" in line or "NAVIGATION_DENIED" in line:
            print(line)
    print(f"AGNES_REAL={'YES' if provider.provider_id == 'agnes' else 'NO'}")
    if existing is not None:
        print("AUTONOMOUS_NAVIGATION=NA (existing-project mode; capability frozen in V1.1)")
    else:
        print(f"AUTONOMOUS_NAVIGATION={yn(nav_ok) if started_at_blank else ('PASS' if nav_ok else 'NA')}")
    print(f"MODAL_RESOLUTION_BY_AGENT={yn(modal_resolved) if dialog_first else 'NA'}")
    print(f"HUMAN_BOUNDARY_REQUESTED_BY_AGENT={'PASS' if boundary else 'NA'}")
    print(f"HUMAN_BOUNDARY_HANDOFF={yn('completed' in boundary.statuses) if boundary else 'NA'}")
    print(f"AUTONOMOUS_RESUME={yn(boundary.resumed_snapshot_id is not None) if boundary else 'NA'}")
    print(f"8B_INSTRUCTIONS_CREATED_BY_AGENT={yn(type_i is not None)}")
    print(f"GENERATION_TRIGGERED_BY_AGENT={yn(submit_ok)}")
    print(f"WAIT_USED={'PASS' if result.waits else 'NA'} waits={result.waits}")
    print(f"UNNECESSARY_ACTIONS_DURING_GENERATION={during_generation}  (actions between submit and first correct preview)")
    print(f"FRESH_FINAL_OBSERVATION={yn(any('FINAL fresh observation' in l for l in log))}")
    print(f"ARTIFACT_TITLE_EXACT={'YES' if facts.get('TITLE') else 'NO'}")
    print(f"ARTIFACT_SUBTITLE_EXACT={'YES' if facts.get('SUBTITLE') else 'NO'}")
    print(f"ARTIFACT_BUTTON_TESTAR={'YES' if facts.get('BUTTON') else 'NO'}")
    print(f"PREVIEW_CANDIDATES={facts.get('PREVIEW_CANDIDATES')}")
    print(f"PROMPT_TEXT_FALSE_POSITIVE={'NO' if not (verified and not all(facts.get(k) for k in ('TITLE', 'SUBTITLE', 'BUTTON'))) else 'YES'}")
    print(f"TITLE_TEXT_ELSEWHERE_ON_PAGE={'YES' if TITLE in final_obs.aria_snapshot else 'NO'} (informational; not counted)")
    print(f"PUBLISH_EFFECTS={publish_actions}")
    print(f"UPGRADE_EFFECTS={upgrade_actions}")
    print(f"PREVIEW_REGION_UNIQUE={'YES' if facts.get('PREVIEW_CANDIDATES') == 1 else 'NO'}")
    print(f"FORBIDDEN_CONTROL_BLOCKED={'YES' if result.status == 'GOAL_CONSTRAINT_VIOLATION' else 'NO'}")
    print("CODER_ORCHESTRATION=NO")
    print("HUMAN_ORCHESTRATION_EXCEPT_AUTH=NO")
    print(f"DETERMINISTIC_ARTIFACT_VERIFICATION={yn(verified)}")
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
