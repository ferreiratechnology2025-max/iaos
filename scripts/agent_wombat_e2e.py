"""IAOS_AGENT_LOOP_V1_2_ARTIFACT_GATE real harness (Website Wombat).

Usage (repo root):
  python -m scripts.agent_wombat_e2e --computer-env <file> --agnes-env <file>

The only agent input is the goal text. The harness SETUP only opens https://websitewombat.app/build
(precondition, not an agent action). It does NOT close the "Start a new site" dialog, pick a template,
point at the prompt box or at "Generate my site". allowed_origins and the forbidden controls
(Publish/Publicar/Upgrade/Download/Copy link/Edit link) are harness-owned constraints, never model input.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

from application.agent_runtime import build_agent_loop
from application.computer_runtime import OpenBotComputerAdapter
from application.daily_brief import configured_agnes_provider
from application.eightb_verifier import BUTTON, SUBTITLE, TITLE
from application.wombat_clean_world import check_clean_world
from application.wombat_verifier import summarize_wombat, verify_wombat_artifact
from cognitive.agent_contracts import AgentGoal
from scripts.agent_todomvc_e2e import AGNES_KEYS, COMPUTER_KEYS, read_env

TARGET = "https://websitewombat.app/build"
ALLOWED = ("https://websitewombat.app",)
FORBIDDEN = ("Publish", "Publicar", "Upgrade", "Download", "Copy link", "Edit link")
GOAL = AgentGoal(
    goal_id="artifact-e2e-wombat",
    objective=(
        "Crie no Website Wombat uma landing page simples de uma p\u00e1gina.\n\n"
        f"T\u00edtulo principal:\n{TITLE}\n\n"
        f"Subt\u00edtulo:\n{SUBTITLE}\n\n"
        f"Inclua um bot\u00e3o:\n{BUTTON}\n\n"
        "N\u00e3o fa\u00e7a download nem crie links de compartilhamento."
    ),
    constraints=(),
    max_steps=60,
)


def yn(v):
    return "PASS" if v else "FAIL"


def mounted_profile_volume(container: str) -> str | None:
    """Name of the docker volume mounted at /profiles in the RUNNING container (the profile identity)."""
    try:
        out = subprocess.run(["docker", "inspect", container, "--format", "{{json .Mounts}}"], capture_output=True,
                             text=True, timeout=30, check=True).stdout
        return next((m.get("Name") for m in json.loads(out) if m.get("Destination") == "/profiles"), None)
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--computer-env", required=True)
    parser.add_argument("--agnes-env", required=True)
    parser.add_argument("--profile-volume", required=True,
                        help="disposable browser-profile volume created for this one gate (openbot-profile-wombat-clean-YYYYMMDD-HHMMSS)")
    parser.add_argument("--profile-ledger", default=r"D:\iaos-secrets\wombat-profiles-used.txt")
    parser.add_argument("--container", default="openbot-computer-takeover-gate")
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
    if control.holder != "bot" or control.requested:
        print(f"PRECONDITION=FAIL holder={control.holder} requested={control.requested}")
        return 3
    # SETUP (harness, not an agent action): open the builder, then verify the preconditions.
    port.observe()  # a fresh snapshot is mandatory on a freshly (re)created computer before the first navigation
    final_url = port.navigate(TARGET)
    time.sleep(6)
    setup = port.observe()
    control = port.read_control_state(None)
    setup_facts = verify_wombat_artifact(setup).facts
    path_ok = final_url.rstrip("/") == TARGET and setup.page_url.rstrip("/") == TARGET
    ledger = []
    if os.path.exists(args.profile_ledger):
        with open(args.profile_ledger, encoding="utf-8") as fh:
            ledger = [line.strip() for line in fh if line.strip()]
    report = check_clean_world(
        requested_volume=args.profile_volume, mounted_volume=mounted_profile_volume(args.container), used_volumes=ledger,
        verification_facts=setup_facts, aria=setup.aria_snapshot, holder=control.holder, requested=control.requested,
        title=TITLE, subtitle=SUBTITLE)
    print(f"INITIAL_PAGE={setup.page_url}")
    print(f"BUILDER_PREBOUND={'YES' if path_ok else 'NO'}")
    for key, value in report.facts.items():
        print(f"{key}={value}")
    print("AUTONOMOUS_NAVIGATION=NA (builder opened by harness SETUP; capability frozen in V1.1)")
    if not (path_ok and report.ok):
        print("CLEAN_WORLD_GATE=FAIL (ABORT before Agnes; agent not started)")
        return 3
    with open(args.profile_ledger, "a", encoding="utf-8") as fh:
        fh.write(args.profile_volume + "\n")
    print("CLEAN_WORLD_GATE=PASS (profile recorded as used; it can never start another gate)")

    records: list[dict] = []

    def summarize(obs):
        facts = verify_wombat_artifact(obs).facts
        records.append({"dialog": "Start a new site" in obs.aria_snapshot, "facts": facts})
        return summarize_wombat(obs)

    provider = configured_agnes_provider()
    loop, session = build_agent_loop(provider, port, verify_wombat_artifact, log=print, summarize=summarize,
                                     allowed_origins=ALLOWED, forbidden_controls=FORBIDDEN, poll_interval=2.0)
    result = loop.run(GOAL)
    log = result.log
    final_obs = port.observe()

    def idx(pred, start_at=0):
        return next((i for i in range(start_at, len(log)) if pred(log[i])), None)

    type_i = idx(lambda l: "Agnes decided browser.type" in l)
    submit_i = idx(lambda l: "Agnes decided browser.key" in l or "Agnes decided browser.click" in l, (type_i or 0) + 1) if type_i is not None else None
    submit_ok = submit_i is not None and idx(lambda l: "action OK" in l, submit_i) is not None
    shown_i = idx(lambda l: "OBSERVATION:" in l and "'TITLE': True" in l, submit_i or 0) if submit_i is not None else None
    window_end = shown_i if shown_i is not None else len(log)
    during = sum(1 for l in log[(submit_i or 0) + 1:window_end] if "Agnes decided browser." in l or "Agnes decided NAVIGATE" in l) if submit_i is not None else None
    dialog_first = records[0]["dialog"] if records else False
    modal_resolved = dialog_first and any(not r["dialog"] for r in records[1:])
    facts = result.verification.facts if result.verification else {}
    verified = result.status == "VERIFIED"
    criterion_corrected = result.progress_events > 0
    overall_corrected = bool(verified and result.failed_finishes > 0)
    effects = {name: sum(1 for c in result.clicks if re.search(rf"\b{pat}\b", c, re.I)) for name, pat in (
        ("DOWNLOAD", "download"), ("COPY_LINK", "copy link"), ("EDIT_LINK", "edit link"),
        ("PUBLISH", "(publish|publicar)"), ("UPGRADE", "upgrade"))}

    print("---")
    print(f"RUN_STATUS={result.status} turns={result.turns} waits={result.waits}")
    print("GOAL_ONLY_INPUT=YES")
    print("ALLOWED_ORIGINS_SOURCE=TRUSTED_RUN_CONFIG")
    print(f"AGNES_REAL={'YES' if provider.provider_id == 'agnes' else 'NO'}")
    print("EXTERNAL_TOOL=Website Wombat")
    print(f"START_NEW_SITE_MODAL_RESOLVED_BY_AGENT={yn(modal_resolved) if dialog_first else 'NA'}")
    print(f"INSTRUCTIONS_CREATED_BY_AGENT={yn(type_i is not None)}")
    print(f"GENERATION_TRIGGERED_BY_AGENT={yn(submit_ok)}")
    print(f"WAIT_USED={'PASS' if result.waits else 'NA'} waits={result.waits}")
    print(f"CRITERION_LEVEL_SELF_CORRECTION={yn(criterion_corrected)} progress_events={result.progress_events}")
    print(f"OVERALL_GOAL_SELF_CORRECTION={yn(overall_corrected)}")
    print(f"STALL_FEEDBACK_DELIVERED={yn(result.stall_feedbacks > 0)} count={result.stall_feedbacks}")
    print(f"ACTION_STAGNATION_DETECTED={yn(result.action_stagnations > 0)} count={result.action_stagnations}")
    print(f"BLOCKED_TARGET_LOGGED={yn(result.blocked_target is not None)} {result.blocked_target or ''}")
    print(f"PREMATURE_FINISH_OCCURRED={yn(result.failed_finishes > 0)} failed_finishes={result.failed_finishes}")
    print(f"VERIFICATION_FEEDBACK_DELIVERED={yn(result.feedback_delivered > 0)} count={result.feedback_delivered}")
    print(f"POST_FAILURE_COGNITION_OCCURRED={yn(result.post_failure_cognition > 0)} count={result.post_failure_cognition}")
    print(f"POST_FAILURE_EXTERNAL_ACTION_OCCURRED={yn(result.post_failure_actions > 0)} count={result.post_failure_actions}")
    print(f"UNNECESSARY_ACTIONS_DURING_GENERATION={during}  (actions between submit and first correct preview)")
    for name, n in effects.items():
        print(f"{name}_EFFECTS={n}")
    print(f"FORBIDDEN_CONTROL_BLOCKED={'YES' if result.status == 'GOAL_CONSTRAINT_VIOLATION' else 'NO'}")
    print(f"FRESH_FINAL_OBSERVATION={yn(any('FINAL fresh observation' in l for l in log))}")
    print(f"PREVIEW_REGION_UNIQUE={'YES' if facts.get('PREVIEW_CANDIDATES') == 1 else 'NO'}")
    print(f"ARTIFACT_TITLE_EXACT={'YES' if facts.get('TITLE') else 'NO'}")
    print(f"ARTIFACT_SUBTITLE_EXACT={'YES' if facts.get('SUBTITLE') else 'NO'}")
    print(f"ARTIFACT_BUTTON_TESTAR={'YES' if facts.get('BUTTON') else 'NO'}")
    print(f"PROMPT_TEXT_FALSE_POSITIVE={'NO' if not (verified and not all(facts.get(k) for k in ('TITLE', 'SUBTITLE', 'BUTTON'))) else 'YES'}")
    print(f"TITLE_TEXT_ELSEWHERE_ON_PAGE={'YES' if TITLE in final_obs.aria_snapshot else 'NO'} (informational; not counted)")
    print("CODER_ORCHESTRATION=NO")
    print("HUMAN_ORCHESTRATION=NO")
    print(f"DETERMINISTIC_ARTIFACT_VERIFICATION={yn(verified)}")
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
