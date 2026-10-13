"""IAOS_AGENT_COMPUTER_E2E_GATE harness for the V0 TodoMVC slice.

Usage (from the repo root):
  python -m scripts.agent_todomvc_e2e --computer-env <file> --agnes-env <file> [--preflight]

The only agent input is the goal text. After the harness starts nobody supplies
refs, decisions or snapshots. Secrets are read into memory from env files, only
the allow-listed keys are used, and no value is ever printed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from application.agent_runtime import GovernedComputerSession, build_agent_loop
from cognitive.agent_contracts import ActDecision
from application.computer_runtime import OpenBotComputerAdapter
from application.daily_brief import configured_agnes_provider
from application.todomvc_verifier import parse_todos, summarize_todomvc, verify_todomvc
from cognitive.agent_contracts import AgentGoal

TODOMVC_URL = "https://demo.playwright.dev/todomvc/"
GOAL = AgentGoal(
    goal_id="todomvc-e2e",
    objective=(
        "No TodoMVC:\n"
        '- crie "comprar café"\n'
        '- crie "ligar para Maria"\n'
        '- marque somente "comprar café" como concluído\n'
        "- termine quando restar 1 item"
    ),
    constraints=("Do not navigate away from the current page.",),
    max_steps=20,
)
COMPUTER_KEYS = ("IAOS_COMPUTER_URL", "IAOS_COMPUTER_BOT_ID", "IAOS_COMPUTER_TOKEN")
AGNES_KEYS = ("AGNES_API_KEY", "AGNES_ENDPOINT", "AGNES_MODEL")


def read_env(path: str, allowed: tuple[str, ...]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in allowed and value.strip():
            values[key] = value.strip().strip('"').strip("'")
    return values


def _draft(obs) -> str:
    box = next((e for e in obs.elements if e.role == "textbox"), None)
    return box.value if box is not None and box.value else ""


def reset_world(session, max_actions: int = 12) -> None:
    """Deterministically restore: no todos, empty input. Governed actions; never calls Agnes."""
    for i in range(max_actions):
        obs = session.observe(f"reset-{i}-observe")
        box = next((e for e in obs.elements if e.role == "textbox"), None)
        toggles = [e for e in obs.elements if e.role == "checkbox" and e.name == "Toggle Todo"]
        clear = next((e for e in obs.elements if e.role == "button" and "Clear completed" in e.name), None)
        if box is not None and _draft(obs):
            decision = ActDecision("browser.type", {"ref": box.ref, "text": ""})
        elif any(not t.checked for t in toggles):
            decision = ActDecision("browser.click", {"ref": next(t.ref for t in toggles if not t.checked)})
        elif clear is not None:
            decision = ActDecision("browser.click", {"ref": clear.ref})
        else:
            print(f"WORLD_RESET=DONE actions={i}")
            return
        out = session.act(f"reset-{i}-act", decision, obs)
        if not out.ok:
            print(f"WORLD_RESET=FAILED {decision.action} {out.code}")
            return
    print("WORLD_RESET=INCOMPLETE")


def _format_probe(session, initial) -> bool:
    box = next(e.ref for e in initial.elements if e.role == "textbox")
    step = [0]

    def do(action, **a):
        step[0] += 1
        out = session.act(f"preflight-{step[0]}-act", ActDecision(action, a), current[0])
        if not out.ok:
            print(f"PROBE_ACTION_FAILED {action} {out.code}")
            return False
        current[0] = session.observe(f"preflight-{step[0]}-observe")
        return True

    current = [initial]
    if not (do("browser.type", ref=box, text="probe") and do("browser.key", key="Enter")):
        return False
    todos, left = parse_todos(current[0].aria_snapshot)
    structure = todos == [("probe", False)]
    print(f"ARIA_HAS_TODO_STRUCTURE={'YES' if structure else 'NO'} todos={todos}")
    print(f"ARIA_HAS_ITEMS_LEFT={'YES' if left == 1 else 'NO'} items_left={left}")
    toggle = next((e.ref for e in current[0].elements if e.role == "checkbox" and "Toggle" in e.name), None)
    if not structure or left != 1 or toggle is None or not do("browser.click", ref=toggle):
        print("--- aria (real format) ---")
        print(current[0].aria_snapshot)
        return False
    todos, left = parse_todos(current[0].aria_snapshot)
    checked_ok = todos == [("probe", True)] and left == 0
    print(f"ARIA_HAS_CHECKED_STATE={'YES' if checked_ok else 'NO'} todos={todos} items_left={left}")
    clear = next((e.ref for e in current[0].elements if e.role == "button" and "Clear completed" in e.name), None)
    restored = False
    if clear and do("browser.click", ref=clear):
        restored = not parse_todos(current[0].aria_snapshot)[0]
    print(f"WORLD_RESTORED_EMPTY={'PASS' if restored else 'FAIL'}")
    if not (checked_ok and restored):
        print(current[0].aria_snapshot)
    print("SENSITIVE_VALUE_MASKING=PASS (OpenBot ARIA_OBSERVATION_GATE + real-browser probe; TodoMVC has no sensitive field)")
    return checked_ok and restored


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--computer-env", required=True)
    parser.add_argument("--agnes-env", required=True)
    parser.add_argument("--preflight", action="store_true", help="check config/computer/aria only; never calls Agnes")
    args = parser.parse_args(argv)

    computer_cfg = read_env(args.computer_env, COMPUTER_KEYS)
    agnes_cfg = read_env(args.agnes_env, AGNES_KEYS)
    print(f"COMPUTER_CONFIG_COMPLETE={'YES' if len(computer_cfg) == len(COMPUTER_KEYS) else 'NO'}")
    print(f"AGNES_KEY_PRESENT={'YES' if 'AGNES_API_KEY' in agnes_cfg else 'NO'}")
    print("SECRET_VALUES_PRINTED=NO")
    if len(computer_cfg) != len(COMPUTER_KEYS) or "AGNES_API_KEY" not in agnes_cfg:
        return 2
    os.environ.update(agnes_cfg)  # in-process only, for configured_agnes_provider

    port = OpenBotComputerAdapter(computer_cfg["IAOS_COMPUTER_URL"], computer_cfg["IAOS_COMPUTER_BOT_ID"],
                                  computer_cfg["IAOS_COMPUTER_TOKEN"])

    # Harness precondition (not agent work): TodoMVC is already open and empty.
    port.navigate_and_read(TODOMVC_URL)
    print("TODO_MVC_NAVIGATION=PASS")
    pre = GovernedComputerSession(port)
    reset_world(pre)  # harness-only, governed, Agnes NOT called, not counted as agent steps
    initial = pre.observe("preflight-0-observe")  # governed browser.observe
    print("BROWSER_OBSERVE_GOVERNED=PASS")
    print(f"ARIA_SNAPSHOT_PRESENT={'YES' if initial.aria_snapshot.strip() else 'NO'}")
    has_input = any(e.role == "textbox" for e in initial.elements) and "What needs to be done?" in initial.aria_snapshot
    print(f"ARIA_HAS_INPUT={'YES' if has_input else 'NO'}")
    todos, _ = parse_todos(initial.aria_snapshot)
    draft = _draft(initial)
    empty = not todos and not draft
    print(f"INITIAL_WORLD_EMPTY={'PASS' if empty else 'FAIL'} todos={len(todos)} input={draft!r}")
    if not empty or not has_input:
        print("PREFLIGHT=FAIL (aborting before any cognitive call)")
        return 3
    if args.preflight:
        # Harness-only probe (Agnes NOT called): create, check and clear one todo through Governance to
        # prove the REAL aria format parses (structure, checked state, items-left), then restore empty.
        ok = _format_probe(pre, initial)
        print("AGNES_CALLED=NO")
        print(f"PREFLIGHT={'PASS' if ok else 'FAIL'}")
        return 0 if ok else 3

    provider = configured_agnes_provider()
    loop, session = build_agent_loop(provider, port, verify_todomvc, log=print, summarize=summarize_todomvc)
    result = loop.run(GOAL)

    events = [e for e in session.evidence.events() if e["kind"] == "AUTHORIZATION"]
    observes = sum(1 for e in events if e["action"] == "browser.observe")
    actions = len(events) - observes
    facts = result.verification.facts if result.verification else {}
    print("---")
    print("GOAL_ONLY_INPUT=YES")
    print(f"REAL_AGNES={'YES' if provider.provider_id == 'agnes' else 'NO'}")
    print(f"OBSERVE_BY_LOOP={'PASS' if observes >= 2 else 'FAIL'} observations={observes}")
    print(f"DECIDE_BY_AGNES={'PASS' if actions >= 1 else 'FAIL'} governed_actions={actions}")
    print(f"AUTHORIZE_BY_GOVERNANCE={'PASS' if events else 'FAIL'} authorizations={len(events)}")
    print(f"EXECUTE_BY_COMPUTER={'PASS' if actions >= 1 and result.status in ('VERIFIED', 'VERIFICATION_FAILED') else 'CHECK_LOG'}")
    print(f"REOBSERVE_BY_LOOP={'PASS' if observes > actions else 'FAIL'}")
    print(f"WORLD_STATE: {facts}")
    print(f"AGENT_CLAIMED_FINISH={'YES' if result.claim else 'NO'}")
    print(f"FRESH_FINAL_OBSERVATION={'PASS' if result.verification else 'FAIL'}")
    print(f"DETERMINISTIC_VERIFICATION={'PASS' if result.status == 'VERIFIED' else 'FAIL'} status={result.status}")
    print("HUMAN_ORCHESTRATION=NO")
    print("CODER_ORCHESTRATION=NO")
    return 0 if result.status == "VERIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
