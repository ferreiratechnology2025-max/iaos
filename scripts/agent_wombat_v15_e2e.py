"""IAOS_AGENT_LOOP_V1_5_MULTI_DOCUMENT_RESEARCH_GATE real harness (NOT RUN until explicitly authorised).

Goal -> governed web.search x2 -> bindings -> governed read_source x2 (controlled return) -> Wombat artifact -> fresh observation -> V1.5 verifier.
Usage (repo root):
  python -m scripts.agent_wombat_v15_e2e --computer-env <f> --agnes-env <f> --profile-volume <new disposable volume>
Trusted run configuration (never model input):
  SEARCH     allowed_domains=["www.rfc-editor.org"], result_limit=1, max_search_attempts=4 (budget v2)
  DEEP_READ  enabled, max_read_attempts=4, target_valid_sources=2, binding=exact search result, controlled_return=YES, one DOCUMENT (canonical URL) per read
  BROWSER    allowed_origins=["https://websitewombat.app"]   (rfc-editor.org is NOT navigable: read_source is the only way)
Scope: MULTI_DOCUMENT_RESEARCH (INDEPENDENT_PUBLISHER_RESEARCH / SOURCE_INDEPENDENCE are NOT targets). One run, no retry, no auto-patch.
The agent input is the goal text only (no deep fact, no title, no URL).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from application.agent_loop import build_prompt
from application.agent_runtime import build_agent_loop
from application.daily_brief import configured_agnes_provider
from application.eightb_verifier import SUBTITLE, TITLE
from application.source_facts import hostname_of
from application.wombat_clean_world import check_clean_world
from application.wombat_v15_verifier import chain_expectations, make_v15_verifier
from application.wombat_verifier import summarize_wombat, verify_wombat_artifact
from governance.aep_executors import BraveSearchExecutor
from governance.bound_read import READ_SEARCH_SOURCE_ACTION, canonical_source_url
from scripts.agent_todomvc_e2e import AGNES_KEYS, COMPUTER_KEYS, read_env
from scripts.agent_wombat_e2e import FORBIDDEN, TARGET, mounted_profile_volume
from scripts.agent_wombat_v13_e2e import RecordingProvider
from scripts.agent_wombat_v14_preflight import RecordingAdapter
from application.wombat_v14_verifier import extract_deep_fact
from scripts.agent_wombat_v15_preflight import ALLOWED, GOAL, MAX_DEEP_READS, RESEARCH, SEARCH, WANTED, WOMBAT, has, identify_rfc, yn

EVIDENCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "evidence")
PREFLIGHT_JSON = os.path.join(EVIDENCE, "v1-5-real-preflight.json")


_NON_MUTATING_CLICK = re.compile(r"^(?:Close|(?:\W\s*)?Start blank|New site|Start building.*|My sites|Home|Page \d+)$")


def first_artifact_mutation(lines: list[str]):
    """Index of the first log line that mutates the ARTIFACT (observational metric; no run behaviour depends on it).

    Mutation: any browser.type / browser.select_option, and any click that is not plain dismissal/navigation (so `+ Page`, `+ Add section`,
    `+ Add block`, `+ Add button` and every content or structural edit count). Not a mutation: Close, Start blank / onboarding dismissal,
    New site, page selection (Home / Page N), links to the builder, browser.key, observe, wait. Unknown click targets count as mutations."""
    for i, line in enumerate(lines):
        m = re.match(r"STEP \d+ Agnes decided (browser\.\w+)", line)
        if not m:
            continue
        if m.group(1) in ("browser.type", "browser.select_option"):
            return i
        if m.group(1) == "browser.click":
            name = None
            for nxt in lines[i + 1:i + 4]:
                t = re.match(r'STEP \d+ TARGET .*? name="(.*)" frame=', nxt)
                if t:
                    name = t.group(1).strip()
                    break
            if name is None or not _NON_MUTATING_CLICK.match(name):
                return i
    return None


def source_value_options() -> dict:
    """V1.5 harness only: the capability and its TRUSTED extractor. The harness never selects source/field/ref: the model chooses."""
    return {"enable_source_value": True, "deep_fact_extractor": extract_deep_fact}


def blocker_recovery_options() -> dict:
    """V1.5 harness only: runtime-derived foreground-blocker context + retention of operational feedback across decision failures."""
    return {"enable_blocker_recovery": True}


def attempt_timeline(lines: list[str]) -> dict:
    """Per-attempt record rebuilt from the loop log: every search attempt and every deep-read attempt, and the VALID source count after each."""
    searches, reads, evolution, valid, pending = [], [], [], 0, None
    for line in lines:
        m = re.search(r"Agnes decided SEARCH query=(.*)$", line)
        if m:
            pending = {"kind": "search", "query": m.group(1), "result_count": 0, "result_url": None, "outcome": None}
            searches.append(pending)
            continue
        m = re.search(r"SEARCH_EXECUTED source=(\S+) .* url='([^']*)'", line)
        if m and pending and pending["kind"] == "search":
            pending.update(result_count=1, result_url=m.group(2), outcome="searched")
            evolution.append(("search", len(searches), valid))
            continue
        m = re.search(r"SEARCH FAILED (\S+)", line)
        if m and pending and pending["kind"] == "search":
            pending.update(outcome=m.group(1))
            evolution.append(("search", len(searches), valid))
            continue
        m = re.search(r"Agnes decided READ_SOURCE source=(\d+)", line)
        if m:
            pending = {"kind": "read", "source": int(m.group(1)), "source_url": None, "transport_success": None, "deep_fact_extracted": None,
                       "admitted_source": "NO", "outcome": None}
            reads.append(pending)
            continue
        m = re.search(r"DEEP_READ_EXECUTED execution=(\S+) url='([^']*)'", line)
        if m and pending and pending["kind"] == "read":
            valid += 1
            pending.update(source_url=m.group(2), transport_success="YES", deep_fact_extracted="YES", admitted_source="YES", outcome="admitted")
            evolution.append(("read", len(reads), valid))
            continue
        m = re.search(r"DEEP_READ FAILED (\S+)", line)
        if m and pending and pending["kind"] == "read":
            code = m.group(1)
            transport = "YES" if code in ("deep_fact_extraction_failed", "source_already_read") or code.startswith(("research_", "deep_read_attempt")) else "NO"
            pending.update(outcome=code, transport_success=transport if code == "deep_fact_extraction_failed" else None,
                           deep_fact_extracted="NO" if code == "deep_fact_extraction_failed" else None)
            evolution.append(("read", len(reads), valid))
    return {"searches": searches, "reads": reads, "valid_sources_after_each": evolution, "valid_sources": valid}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--computer-env", required=True)
    parser.add_argument("--agnes-env", required=True)
    parser.add_argument("--profile-volume", required=True)
    parser.add_argument("--profile-ledger", default=r"D:\iaos-secrets\wombat-profiles-used.txt")
    parser.add_argument("--container", default="openbot-computer-takeover-gate")
    args = parser.parse_args(argv)
    computer_cfg = read_env(args.computer_env, COMPUTER_KEYS)
    agnes_cfg = read_env(args.agnes_env, AGNES_KEYS)
    brave_cfg = read_env(args.agnes_env, ("BRAVE_SEARCH_API_KEY",))
    print(f"COMPUTER_CONFIG_COMPLETE={yn(len(computer_cfg) == len(COMPUTER_KEYS))} AGNES_KEY_PRESENT={yn('AGNES_API_KEY' in agnes_cfg)} "
          f"BRAVE_KEY_PRESENT={yn('BRAVE_SEARCH_API_KEY' in brave_cfg)} SECRET_VALUES_PRINTED=NO")
    if len(computer_cfg) != len(COMPUTER_KEYS) or "AGNES_API_KEY" not in agnes_cfg or "BRAVE_SEARCH_API_KEY" not in brave_cfg:
        return 2
    os.environ.update(agnes_cfg)
    os.environ.update(brave_cfg)
    port = RecordingAdapter(computer_cfg["IAOS_COMPUTER_URL"], computer_cfg["IAOS_COMPUTER_BOT_ID"], computer_cfg["IAOS_COMPUTER_TOKEN"])
    control = port.read_control_state(None)
    if control.holder != "bot" or control.requested:
        print(f"PRECONDITION=FAIL holder={control.holder} requested={control.requested}")
        return 3
    port.observe()
    final_url = port.navigate(TARGET)
    time.sleep(6)
    setup = port.observe()
    control = port.read_control_state(None)
    path_ok = final_url.rstrip("/") == TARGET and setup.page_url.rstrip("/") == TARGET
    used = []
    if os.path.exists(args.profile_ledger):
        with open(args.profile_ledger, encoding="utf-8") as fh:
            used = [line.strip() for line in fh if line.strip()]
    mounted = mounted_profile_volume(args.container)
    report = check_clean_world(requested_volume=args.profile_volume, mounted_volume=mounted, used_volumes=used,
                               verification_facts=verify_wombat_artifact(setup).facts, aria=setup.aria_snapshot, holder=control.holder,
                               requested=control.requested, title=TITLE, subtitle=SUBTITLE)
    print(f"INITIAL_PAGE={setup.page_url} BUILDER_PREBOUND={yn(path_ok)} "
          f"NEW_DISPOSABLE_PROFILE={yn(args.profile_volume not in used and mounted == args.profile_volume)}")
    for key, value in report.facts.items():
        print(f"{key}={value}")
    print(f"holder={control.holder} pending_takeover={yn(control.requested)}")

    # Pre-run absence proof with the six values the real preflight observed (re-checked against the live values after the run).
    with open(PREFLIGHT_JSON, encoding="utf-8") as fh:
        pre = json.load(fh)
    six = []
    for item, rep in zip(pre["items"], pre["reports"]):
        six += [item["title"], item["url"], rep["DEEP_FACT"]]
    first_prompt = build_prompt(GOAL, setup, 1, None, ALLOWED, None, "", True, True, MAX_DEEP_READS, RESEARCH, True)
    prechecks = {f"VALUE_{i + 1}_NOT_IN_INITIAL_OBSERVATION": not has(setup.aria_snapshot, v) for i, v in enumerate(six)}
    prechecks.update({f"VALUE_{i + 1}_NOT_IN_GOAL": not has(GOAL.objective, v) for i, v in enumerate(six)})
    prechecks.update({f"VALUE_{i + 1}_NOT_IN_INITIAL_PROMPT": not has(first_prompt, v) for i, v in enumerate(six)})
    prechecks["RFC_EDITOR_NOT_A_BROWSER_ORIGIN"] = all("rfc-editor" not in o for o in ALLOWED)
    for key, value in prechecks.items():
        print(f"{key}={yn(value)}")
    if not (path_ok and report.ok and all(prechecks.values())):
        print("CLEAN_WORLD_GATE=FAIL (ABORT before Agnes; agent not started)")
        return 3
    with open(args.profile_ledger, "a", encoding="utf-8") as fh:
        fh.write(args.profile_volume + "\n")
    print("CLEAN_WORLD_GATE=PASS (profile recorded as used; it can never start another gate)")
    print("PROFILE_UNUSED=YES TARGET_VALID_SOURCES=0 SEARCH_ATTEMPTS=0 DEEP_READ_ATTEMPTS=0 TARGET_ARTIFACT_ALREADY_PRESENT=NO")

    lines: list[str] = []
    prompt_count_at_line: list[int] = []
    holder: dict = {}

    def log(line: str) -> None:
        lines.append(line)
        prompt_count_at_line.append(len(holder["provider"].prompts) if "provider" in holder else 0)
        print(line)

    executor = BraveSearchExecutor()
    observations: list[dict] = []

    def valid_ledger():
        """Ledger entries of VALID sources only (admitted DeepReadFact). Failed/empty search attempts stay in the full ledger and trace;
        the frozen V1.5 verifier judges the two valid chains."""
        ids = {f.search_execution_id for f in holder["loop"].deep_read_facts}
        return [entry for entry in holder["session"].search_ledger if entry["execution_id"] in ids]

    verifier = make_v15_verifier(valid_ledger, lambda: holder["session"].evidence.events(),
                                 lambda: tuple(executor._effects), lambda: holder["session"].bindings,
                                 lambda: holder["loop"].deep_read_facts, lambda: holder["session"]._read_executor.effects())

    def summarize(obs):
        observations.append({"log_index": len(lines), "facts": dict(verifier(obs).facts), "aria": obs.aria_snapshot, "page_url": obs.page_url})
        return summarize_wombat(obs)

    provider = RecordingProvider(configured_agnes_provider())
    holder["provider"] = provider
    loop, session = build_agent_loop(provider, port, verifier, log=log, summarize=summarize, allowed_origins=ALLOWED,
                                     forbidden_controls=FORBIDDEN, poll_interval=2.0, search=SEARCH, search_executor=executor,
                                     allow_read=True, research=RESEARCH,
                                     deep_fact_validator=lambda fact: extract_deep_fact(fact.visible_text_excerpt) is not None,
                                     **source_value_options(), **blocker_recovery_options())
    holder["session"], holder["loop"] = session, loop
    result = loop.run(GOAL)
    # Raw evidence BEFORE any browser call or metric: nothing can lose the run.
    with open(os.path.join(EVIDENCE, "v1-5-live-raw.json"), "w", encoding="utf-8") as fh:
        json.dump({"agent_status": result.status, "turns": result.turns, "log": lines, "prompts": provider.prompts,
                   "events": [dict(e) for e in session.evidence.events()], "ledger": session.search_ledger,
                   "bindings": [b.__dict__ for b in session.bindings], "observations": observations,
                   "read_trace": list(session._read_executor.trace), "http_posts": [[p, b] for m, p, b in port.calls if m == "POST"],
                   "clicks": list(result.clicks), "deep_facts": [dict(f.__dict__) for f in loop.deep_read_facts]},
                  fh, ensure_ascii=False, indent=1, default=str)
    try:
        final_obs_url = port.observe().page_url
    except Exception as exc:
        final_obs_url = f"FINAL_OBSERVE_FAILED:{type(exc).__name__}"
    events = session.evidence.events()
    deep_facts = loop.deep_read_facts
    chains, distinct_fail = chain_expectations(valid_ledger(), events, tuple(executor._effects), session.bindings, deep_facts,
                                               session._read_executor.effects())
    verified = result.status == "VERIFIED"
    facts = result.verification.facts if result.verification else {}
    prompts = provider.prompts
    search_lines = [i for i, l in enumerate(lines) if "SEARCH_EXECUTED" in l]
    read_lines = [i for i, l in enumerate(lines) if "DEEP_READ_EXECUTED" in l]
    first_act = next((i for i, l in enumerate(lines) if "Agnes decided browser." in l), None)
    rtrace = list(session._read_executor.trace)
    posts = [(p, b) for m, p, b in port.calls if m == "POST"]
    rfc_posts = [(p, b) for p, b in posts if isinstance(b, dict) and ("rfc-editor" in str(b.get("url")) )]
    deep_vals = [c[2]["deep_fact"] for c in chains if c[2]]

    def norm(t: str) -> str:
        return " ".join(t.replace("| ", " ").split())

    after_second = prompts[prompt_count_at_line[read_lines[1]]:] if len(read_lines) >= 2 else []
    persisted_both = bool(after_second) and all(all(v in norm(p) for v in deep_vals) for p in after_second) and len(deep_vals) == 2
    items = [{"title": e["title"], "url": e["url"]} for e in valid_ledger()]   # identity of the VALID sources
    ident = [identify_rfc(i) for i in items]
    control_requests = [p for m, p, _ in port.calls if p.startswith("/control/request")]
    human_events = [e for e in events if e.get("action") == "browser.request_human"]
    read_done = [e for e in events if e.get("kind") == "EXECUTION_RESULT" and e.get("action") == READ_SEARCH_SOURCE_ACTION and e.get("ok") is True]
    read_failed = [l for l in lines if "DEEP_READ FAILED" in l]
    click_effects = {name: sum(1 for c in result.clicks if re.search(rf"\b{pat}\b", c, re.I)) for name, pat in (
        ("DOWNLOAD", "download"), ("COPY_LINK", "copy link"), ("EDIT_LINK", "edit link"), ("PUBLISH", "(publish|publicar)"), ("UPGRADE", "upgrade"))}
    different_docs = tuple(ident) != WANTED
    precondition_failed = different_docs or (not verified) and any(
        any(c in l for c in ("challenge_detected", "final_url_mismatch", "final_url_invalid")) for l in read_failed)
    run_status = "SOURCE_PRECONDITION_FAILED" if precondition_failed else result.status

    print("---")
    print(f"RUN_STATUS={run_status} agent_status={result.status} turns={result.turns} waits={result.waits} searches={result.searches} deep_reads={result.deep_reads}")
    print("ONE_HIGH_LEVEL_GOAL=YES MULTI_DOCUMENT_RESEARCH_TARGET=YES INDEPENDENT_PUBLISHER_RESEARCH_TARGET=NO")
    print(f"AGNES_REAL={yn(provider.provider_id == 'agnes')}")
    print(f"SEARCHES={result.searches} SEARCH_RESULT_LIMIT={SEARCH.result_limit} MAX_SEARCHES={SEARCH.max_searches} DEEP_READ_COUNT={result.deep_reads} MAX_SEARCH_ATTEMPTS={RESEARCH.max_search_attempts} MAX_DEEP_READ_ATTEMPTS={RESEARCH.max_deep_read_attempts} TARGET_VALID_SOURCES={RESEARCH.target_valid_sources}")
    print(f"SOURCE_RFC2616_IDENTIFIED={yn(2616 in ident)} SOURCE_RFC793_IDENTIFIED={yn(793 in ident)} identified={ident}")
    print(f"SEARCH_ALLOWED_DOMAIN_ENFORCED={yn(bool(items) and all(hostname_of(i['url']) in SEARCH.allowed_domains for i in items))}")
    for label, c in zip("AB", chains):
        e = c[2]
        print(f"CHAIN_{label}_CORRELATED={yn(e is not None)} reason={c[3]}")
        if e:
            print(f"CHAIN_{label}_TITLE={e['title']!r} URL={e['url']!r} DEEP_FACT={e['deep_fact']!r} TRUNCATED={yn(e['truncated'])}")
            print(f"CHAIN_{label}_SEARCH_EXECUTION={e['search_execution_id']} READ_EXECUTION={e['read_execution_id']}")
    print(f"CHAINS_DISTINCT={yn(not distinct_fail and all(c[2] for c in chains))} failures={distinct_fail}")
    print(f"GOVERNED_DEEP_READ_USED={yn(len(read_done) >= 1)} READ_EXECUTION_RESULTS={len(read_done)}")
    print(f"REQUEST_HELP_FALSE={'PASS' if all(b.get('requestHelp') is False for p, b in posts if p in ('/snapshot', '/navigate') and isinstance(b, dict) and 'requestHelp' in b) else 'FAIL'}")
    print(f"CHALLENGE={yn(any('challenge_detected' in l for l in read_failed))} TAKEOVER_REQUESTS={len(control_requests) + len(human_events)}")
    print(f"READS_VISITED_BOUND_URLS={yn(all(any(isinstance(b, dict) and b.get('url') == bd.canonical_url for p, b in rfc_posts) for bd in session.bindings))}")
    for i, t in enumerate(rtrace, 1):
        print(f"READ{i}_TRACE source_final={t.get('source_final_url')} return_url={t.get('return_url')} restored={t.get('return_observed_url')} "
              f"attempted={t.get('restore_attempted')} succeeded={t.get('restore_succeeded')}")
    print(f"CONTROLLED_RETURN_BOTH={yn(len(rtrace) == 2 and all(t.get('restore_succeeded') for t in rtrace))}")
    print(f"SEARCH_BEFORE_READS={yn(len(search_lines) == 2 and len(read_lines) == 2 and max(search_lines) < min(read_lines))} (informational: interleaving is allowed)")
    print(f"BOTH_DEEP_READS_BEFORE_FIRST_WOMBAT_MUTATION={yn(len(read_lines) == 2 and (first_act is None or max(read_lines) < first_act))} first_wombat_action_log_index={first_act}")
    print(f"BOTH_DEEP_FACTS_PERSISTED_AFTER_SECOND_READ={yn(persisted_both)} prompts_after_second_read={len(after_second)}")
    print(f"AGENT_NAVIGATED_TO_SOURCE_DOMAIN_VIA_NAVIGATE={sum(1 for l in lines if 'REQUESTED_ORIGIN=' in l and 'rfc-editor' in l)}")
    print(f"FRESH_FINAL_OBSERVATION={'PASS' if any('FINAL fresh observation' in l for l in lines) else 'FAIL'}")
    print(f"PREVIEW_REGION_UNIQUE={yn(facts.get('SEARCH_CHAINS') == 2 and facts.get('CHAIN_A_VERIFIED') is not None)} (verified through the V1.4 rules of each chain)")
    for k in ("CHAIN_A_CORRELATED", "CHAIN_B_CORRELATED", "CHAIN_A_VERIFIED", "CHAIN_B_VERIFIED", "CHAINS_DISTINCT"):
        print(f"VERIFIER_{k}={yn(facts.get(k))}")
    print(f"PREMATURE_FINISH_OCCURRED={yn(result.failed_finishes > 0)} failed_finishes={result.failed_finishes} ACTION_STAGNATION={result.action_stagnations}")
    for name, n in click_effects.items():
        print(f"{name}_EFFECTS={n}")
    print("CODER_INTERVENTION=NO HUMAN_INTERVENTION=NO")
    tl = attempt_timeline(lines)
    for i, a in enumerate(tl["searches"], 1):
        print(f"SEARCH_ATTEMPT_{i}: query={a['query']} result_count={a['result_count']} result_url={a['result_url']} outcome={a['outcome']}")
    for i, a in enumerate(tl["reads"], 1):
        print(f"DEEP_READ_ATTEMPT_{i}: source={a['source']} source_url={a['source_url']} transport_success={a['transport_success']} "
              f"deep_fact_extracted={a['deep_fact_extracted']} admitted_source={a['admitted_source']} outcome={a['outcome']}")
    print(f"VALID_SOURCES_AFTER_EACH_ATTEMPT={tl['valid_sources_after_each']}")
    print(f"VALID_SOURCE_COUNT={tl['valid_sources']} SEARCH_ATTEMPTS_USED={result.searches} DEEP_READ_ATTEMPTS_USED={result.deep_reads}")
    two_valid_line = read_lines[1] if len(read_lines) >= 2 else None
    first_act_line = lines[first_act] if first_act is not None else None
    print(f"LEGACY_TWO_VALID_DEEP_READ_FACTS_BEFORE_FIRST_WOMBAT_ACTION={yn(two_valid_line is not None and (first_act is None or two_valid_line < first_act))} "
          f"first_wombat_action={first_act_line!r}")
    art = first_artifact_mutation(lines)
    print(f"TWO_VALID_DEEP_READ_FACTS_BEFORE_FIRST_ARTIFACT_MUTATION={yn(two_valid_line is not None and (art is None or two_valid_line < art))} "
          f"first_artifact_mutation_log_index={art} first_artifact_mutation={lines[art] if art is not None else None!r}")
    for i, u in enumerate(result.source_value_uses, 1):
        print(f"SOURCE_VALUE_USE_{i}: turn={u.turn} target_ref={u.ref} source={u.source} field={u.field} digest={u.value_digest[:12]} length={u.value_length}")
    print(f"SOURCE_VALUE_USED={yn(bool(result.source_value_uses))}")
    print("CONTROLLED_RETURN_RESTORES_NAVIGATION=YES CONTROLLED_RETURN_RESTORES_PAGE_INTERNAL_STATE=NO TEMPORARY_SOURCE_NETWORK_EFFECT_OCCURRED=YES NETWORK_EFFECT_ROLLBACK=NO")
    print(f"DETERMINISTIC_V1_5_VERIFICATION={'PASS' if verified else 'FAIL'}")
    with open(os.path.join(EVIDENCE, "v1-5-live-trace.json"), "w", encoding="utf-8") as fh:
        json.dump({"status": run_status, "agent_status": result.status, "log": lines, "prompts_count": len(prompts), "ledger": session.search_ledger,
                   "final_url": final_obs_url, "http_posts": [[p, b] for p, b in posts], "deep_values": deep_vals}, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(EVIDENCE, "v1-5-live-prompts.json"), "w", encoding="utf-8") as fh:
        json.dump(prompts, fh, ensure_ascii=False, indent=1)
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
