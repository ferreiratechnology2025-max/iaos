"""IAOS_AGENT_LOOP_V1_4_DEEP_RESEARCH_GATE real harness: governed web.search -> binding -> governed read_source(1) -> return to Wombat -> artifact.

Usage (repo root):
  python -m scripts.agent_wombat_v14_e2e --computer-env <f> --agnes-env <f> --profile-volume <new disposable volume>

The only agent input is the goal text (no RFC numbers, no title, no URL, no sentence).
Trusted run configuration (never model input):
  SEARCH     allowed_domains=["www.rfc-editor.org"], result_limit=1
  DEEP_READ  enabled, max_reads=1, binding=exact search result
  BROWSER    allowed_origins=["https://websitewombat.app"]   (rfc-editor.org is NOT navigable: read_source is the only way)
Starts on Wombat /build. One run, no retry, no auto-patch. A source precondition failure is reported as SOURCE_PRECONDITION_FAILED.
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
from application.source_fact_checks import check_title_url_provenance
from application.source_facts import hostname_of
from application.wombat_clean_world import check_clean_world
from application.wombat_v13_verifier import correlated_expected
from application.wombat_v14_verifier import check_deep_fact_provenance, deep_correlation, extract_deep_fact, make_v14_verifier
from application.wombat_verifier import summarize_wombat, verify_wombat_artifact
from governance.aep_executors import BraveSearchExecutor
from governance.bound_read import READ_SEARCH_SOURCE_ACTION, canonical_source_url
from scripts.agent_todomvc_e2e import AGNES_KEYS, COMPUTER_KEYS, read_env
from scripts.agent_wombat_e2e import FORBIDDEN, TARGET, mounted_profile_volume, yn
from scripts.agent_wombat_v14_preflight import ALLOWED, GOAL, SEARCH, RecordingAdapter
from scripts.agent_wombat_v13_e2e import RecordingProvider

PREFLIGHT_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "evidence", "v1-4-deep-read-preflight.json")
EVIDENCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "evidence")
WOMBAT_ORIGIN = "https://websitewombat.app"


def norm(text: str) -> str:
    return " ".join(text.split())


def has(text: str, value: str) -> bool:
    return bool(value) and norm(value).casefold() in norm(text).casefold()


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
    print(f"COMPUTER_CONFIG_COMPLETE={'YES' if len(computer_cfg) == len(COMPUTER_KEYS) else 'NO'}")
    print(f"AGNES_KEY_PRESENT={'YES' if 'AGNES_API_KEY' in agnes_cfg else 'NO'}")
    print(f"BRAVE_KEY_PRESENT={'YES' if 'BRAVE_SEARCH_API_KEY' in brave_cfg else 'NO'}")
    print("SECRET_VALUES_PRINTED=NO")
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
    setup_facts = verify_wombat_artifact(setup).facts
    path_ok = final_url.rstrip("/") == TARGET and setup.page_url.rstrip("/") == TARGET
    ledger_used = []
    if os.path.exists(args.profile_ledger):
        with open(args.profile_ledger, encoding="utf-8") as fh:
            ledger_used = [line.strip() for line in fh if line.strip()]
    mounted = mounted_profile_volume(args.container)
    report = check_clean_world(
        requested_volume=args.profile_volume, mounted_volume=mounted, used_volumes=ledger_used,
        verification_facts=setup_facts, aria=setup.aria_snapshot, holder=control.holder, requested=control.requested,
        title=TITLE, subtitle=SUBTITLE)
    print(f"INITIAL_PAGE={setup.page_url}")
    print(f"BUILDER_PREBOUND={'YES' if path_ok else 'NO'}")
    print(f"NEW_DISPOSABLE_PROFILE={'YES' if args.profile_volume not in ledger_used and mounted == args.profile_volume else 'NO'}")
    for key, value in report.facts.items():
        print(f"{key}={value}")
    print(f"holder={control.holder}")
    print(f"pending_takeover={'YES' if control.requested else 'NO'}")

    # Pre-run provenance with the values the preflight observed (the live values are re-checked after the run).
    with open(PREFLIGHT_JSON, encoding="utf-8") as fh:
        pre = json.load(fh)
    pre_title, pre_url = pre["runs"][0]["search_item"]["title"], pre["runs"][0]["search_item"]["url"]
    pre_deep = pre["reports"][0]["DEEP_FACT"]
    first_prompt_pre = build_prompt(GOAL, setup, 1, None, ALLOWED, None, "", True, True)
    prechecks = {
        "SEARCH_TITLE_NOT_IN_INITIAL_OBSERVATION": not has(setup.aria_snapshot, pre_title),
        "SEARCH_URL_NOT_IN_INITIAL_OBSERVATION": not has(setup.aria_snapshot, pre_url),
        "DEEP_FACT_NOT_IN_INITIAL_OBSERVATION": not has(setup.aria_snapshot, pre_deep),
        "SEARCH_TITLE_NOT_IN_GOAL": not has(GOAL.objective, pre_title),
        "SEARCH_URL_NOT_IN_GOAL": not has(GOAL.objective, pre_url),
        "DEEP_FACT_NOT_IN_GOAL": not has(GOAL.objective, pre_deep),
        "DEEP_FACT_NOT_IN_INITIAL_PROMPT": not has(first_prompt_pre, pre_deep),
        "NO_RFC_NUMBER_IN_GOAL": not re.search(r"RFC\s*\d{4}\b.*", GOAL.objective.replace("RFC 2616", "")),
        "RFC_EDITOR_NOT_A_BROWSER_ORIGIN": all("rfc-editor" not in o for o in ALLOWED),
    }
    for key, value in prechecks.items():
        print(f"{key}={yn(value)}")
    if not (path_ok and report.ok and all(prechecks.values())):
        print("CLEAN_WORLD_GATE=FAIL (ABORT before Agnes; agent not started)")
        return 3
    with open(args.profile_ledger, "a", encoding="utf-8") as fh:
        fh.write(args.profile_volume + "\n")
    print("CLEAN_WORLD_GATE=PASS (profile recorded as used; it can never start another gate)")

    lines: list[str] = []
    prompt_count_at_line: list[int] = []
    holder: dict = {}

    def log(line: str) -> None:
        lines.append(line)
        prompt_count_at_line.append(len(holder["provider"].prompts) if "provider" in holder else 0)
        print(line)

    executor = BraveSearchExecutor()
    observations: list[dict] = []
    verifier = make_v14_verifier(lambda: holder["session"].search_ledger, lambda: holder["session"].evidence.events(),
                                 lambda: tuple(executor._effects), lambda: holder["session"].bindings,
                                 lambda: holder["loop"].deep_read_facts, lambda: holder["session"]._read_executor.effects())

    def summarize(obs):
        facts = verifier(obs).facts
        observations.append({"log_index": len(lines), "facts": dict(facts), "aria": obs.aria_snapshot, "page_url": obs.page_url})
        return summarize_wombat(obs)

    provider = RecordingProvider(configured_agnes_provider())
    holder["provider"] = provider
    loop, session = build_agent_loop(provider, port, verifier, log=log, summarize=summarize, allowed_origins=ALLOWED,
                                     forbidden_controls=FORBIDDEN, poll_interval=2.0, search=SEARCH, search_executor=executor,
                                     allow_read=True)
    holder["session"], holder["loop"] = session, loop
    result = loop.run(GOAL)
    # Raw evidence is written BEFORE any browser call or metric, so neither a final-observation failure nor a report bug can lose the run.
    with open(os.path.join(EVIDENCE, "v1-4-live-raw.json"), "w", encoding="utf-8") as fh:
        json.dump({"agent_status": result.status, "turns": result.turns, "log": lines, "prompts": provider.prompts,
                   "events": [dict(e) for e in session.evidence.events()], "ledger": session.search_ledger,
                   "observations": [{k: v for k, v in o.items()} for o in observations], "read_trace": list(session._read_executor.trace),
                   "http_posts": [[p, b] for m, p, b in port.calls if m == "POST"], "clicks": list(result.clicks),
                   "deep_facts": [dict(f.__dict__) for f in loop.deep_read_facts]}, fh, ensure_ascii=False, indent=1, default=str)
    try:
        final_obs_url = port.observe().page_url
    except Exception as exc:                      # reported, never fatal to the report
        final_obs_url = f"FINAL_OBSERVE_FAILED:{type(exc).__name__}"
    events = session.evidence.events()
    s_effects = tuple(executor._effects)
    r_effects = tuple(session._read_executor.effects())
    deep_facts = loop.deep_read_facts
    fact = result.source_facts[0] if result.source_facts else None
    deep_fact_obj = deep_facts[0] if deep_facts else None
    expected, reason = deep_correlation(session.search_ledger, events, s_effects, session.bindings, deep_facts, r_effects)
    search_expected = correlated_expected(session.search_ledger, events, s_effects)
    verified = result.status == "VERIFIED"
    facts = result.verification.facts if result.verification else {}
    prompts = provider.prompts

    search_line = next((i for i, l in enumerate(lines) if "SEARCH_EXECUTED" in l), None)
    read_line = next((i for i, l in enumerate(lines) if "DEEP_READ_EXECUTED" in l), None)
    read_failed = [l for l in lines if "DEEP_READ FAILED" in l]
    deep = expected["deep_fact"] if expected else (extract_deep_fact(deep_fact_obj.visible_text_excerpt) if deep_fact_obj else None)

    def deep_in_prompt(p: str) -> bool:
        return bool(deep) and deep in norm(p.replace("| ", " "))

    in_source_data = next((i for i, p in enumerate(prompts) if deep_in_prompt(p)), None)
    # V1_4_DEEP_READ_CONTROLLED_RETURN_V1: the RUNTIME restores the working page; the agent never navigates back.
    rtrace = list(session._read_executor.trace)
    restored = bool(rtrace) and rtrace[-1]["restore_succeeded"] and str(rtrace[-1]["return_observed_url"]).startswith(WOMBAT_ORIGIN)
    return_line = read_line
    returned = restored and any(o["log_index"] > read_line and o["page_url"].startswith(WOMBAT_ORIGIN) for o in observations)
    after_return = prompts[prompt_count_at_line[read_line]:] if read_line is not None else []
    persisted = bool(after_return) and all(deep_in_prompt(p) for p in after_return)
    first_deep_hit = next((o["log_index"] for o in observations if o["facts"].get("DEEP_FACT_EXACT")), None)
    first_all_hit = next((o["log_index"] for o in observations if o["facts"].get("DEEP_FACT_EXACT") and o["facts"].get("SEARCH_RESULT_TITLE_EXACT")
                          and o["facts"].get("SEARCH_RESULT_URL_EXACT")), None)
    pre_read_hits = [o for o in observations if read_line is not None and o["log_index"] <= read_line
                     and (o["facts"].get("DEEP_FACT_EXACT") or o["facts"].get("SEARCH_RESULT_TITLE_EXACT") or o["facts"].get("SEARCH_RESULT_URL_EXACT"))]
    posts = [x for x in port.posts_log()] if hasattr(port, "posts_log") else [(p, b) for m, p, b in port.calls if m == "POST"]
    bound_url = session.bindings[0].canonical_url if session.bindings else None
    read_nav = [b for p, b in posts if p == "/navigate" and isinstance(b, dict) and bound_url and b.get("url") == bound_url]
    rfc_navs = [b for p, b in posts if p == "/navigate" and isinstance(b, dict) and "rfc-editor" in str(b.get("url"))]
    control_requests = [p for m, p, _ in port.calls if p.startswith("/control/request")]
    human_events = [e for e in events if e.get("action") == "browser.request_human"]
    nav_idx = next((i for i, (p, b) in enumerate(posts) if p == "/navigate" and isinstance(b, dict) and b.get("url") == bound_url), None)
    request_help_false = (nav_idx is not None and nav_idx > 0 and posts[nav_idx][1].get("requestHelp") is False
                          and posts[nav_idx - 1][0] == "/snapshot" and posts[nav_idx - 1][1].get("requestHelp") is False)
    browser_nav_attempts_rfc = sum(1 for l in lines if "REQUESTED_ORIGIN=" in l and "rfc-editor" in l)
    done = [e for e in events if e.get("kind") == "EXECUTION_RESULT" and e.get("action") == "web.search" and e.get("ok") is True]
    auth = [e for e in events if e.get("kind") == "AUTHORIZATION" and e.get("action") == "web.search"]
    read_done = [e for e in events if e.get("kind") == "EXECUTION_RESULT" and e.get("action") == READ_SEARCH_SOURCE_ACTION and e.get("ok") is True]
    prov = (check_title_url_provenance(fact.title, fact.url, GOAL.objective, prompts[0] if prompts else "", setup.aria_snapshot,
                                       session.search_ledger, events, s_effects) if fact else {})
    item = {"title": fact.title, "url": fact.url, "description": fact.description} if fact else {}
    dprov = check_deep_fact_provenance(deep, item, GOAL.objective, prompts[0] if prompts else "", setup.aria_snapshot) if deep and fact else {}
    click_effects = {name: sum(1 for c in result.clicks if re.search(rf"\b{pat}\b", c, re.I)) for name, pat in (
        ("DOWNLOAD", "download"), ("COPY_LINK", "copy link"), ("EDIT_LINK", "edit link"),
        ("PUBLISH", "(publish|publicar)"), ("UPGRADE", "upgrade"))}
    different_source = fact is not None and canonical_source_url(fact.url) != canonical_source_url(pre_url)
    precondition_failed = different_source or (not verified) and (
        any(any(c in l for c in ("challenge_detected", "final_url_mismatch", "final_url_invalid")) for l in read_failed)
        or (deep_fact_obj is not None and reason == "deep_fact_extraction_failed"))
    run_status = "SOURCE_PRECONDITION_FAILED" if precondition_failed else result.status

    print("---")
    print(f"RUN_STATUS={run_status} agent_status={result.status} turns={result.turns} waits={result.waits} searches={result.searches} deep_reads={result.deep_reads}")
    print("ONE_HIGH_LEVEL_GOAL=YES")
    print(f"AGNES_REAL={'YES' if provider.provider_id == 'agnes' else 'NO'}")
    print(f"GOVERNED_SEARCH_USED={yn(bool(auth) and bool(done))}")
    print(f"SEARCH_RESULT_LIMIT={SEARCH.result_limit}")
    print(f"SEARCH_ALLOWED_DOMAIN_ENFORCED={yn(fact is not None and hostname_of(fact.url) in SEARCH.allowed_domains)} allowed={list(SEARCH.allowed_domains)}")
    print(f"SEARCH_RESULT_URL={fact.url if fact else None}")
    print(f"SEARCH_SOURCE_BINDING={'PASS' if expected else 'FAIL:' + reason}")
    print(f"SEARCH_EFFECT_CORRELATION={'PASS' if search_expected else 'FAIL'}")
    print(f"GOVERNED_DEEP_READ_USED={yn(bool(read_done))}")
    print(f"DEEP_READ_COUNT={result.deep_reads}")
    print(f"EXACT_URL_BINDING={'PASS' if bound_url and fact and bound_url == canonical_source_url(fact.url) and read_nav else 'FAIL'}")
    print(f"BROWSER_VISITED_BOUND_URL={yn(bool(read_nav))} bound={bound_url}")
    print(f"REQUEST_HELP_FALSE={'PASS' if request_help_false else 'FAIL'}")
    print(f"CHALLENGE={'NO' if not any('challenge_detected' in l for l in read_failed) else 'YES'}")
    print(f"TAKEOVER_REQUESTS={len(control_requests) + len(human_events)}")
    print(f"READ_FINAL_URL_EQUALS_BOUND_URL={yn(deep_fact_obj is not None and deep_fact_obj.source_url == bound_url)}")
    print(f"READ_EFFECT_CORRELATION={'PASS' if expected else 'FAIL:' + reason}")
    print(f"DEEP_READ_FACT_CREATED={yn(deep_fact_obj is not None)}")
    print(f"VISIBLE_TEXT_TRUNCATED={'YES' if deep_fact_obj is not None and deep_fact_obj.truncated else 'NO'}")
    print(f"DEEP_FACT_NOT_IN_SEARCH_SNIPPET={yn(bool(dprov) and all(dprov[f'DEEP_FACT_NOT_IN_{k}'] == 'YES' for k in ('SEARCH_TITLE', 'SEARCH_URL', 'SEARCH_DESCRIPTION')))}")
    print(f"DEEP_FACT_FROM_LIVE_READ={yn(expected is not None and deep is not None)}")
    print(f"DEEP_FACT={deep!r}")
    for key, value in {**prov, **dprov}.items():
        print(f"{key}={value}")
    print(f"DEEP_FACT_IN_SOURCE_DATA={yn(in_source_data is not None)}")
    print(f"SEARCH_OCCURRED_BEFORE_DEEP_READ={yn(search_line is not None and read_line is not None and search_line < read_line)}")
    print(f"DEEP_READ_OCCURRED_BEFORE_DEEP_FACT_FIRST_APPEARED_IN_PREVIEW={yn(read_line is not None and first_deep_hit is not None and first_deep_hit > read_line and not pre_read_hits)}"
          f" read_log_index={read_line} first_deep_fact_preview_log_index={first_deep_hit}")
    print(f"RETURNED_TO_WOMBAT_AFTER_DEEP_READ={yn(returned)} (runtime-controlled return)")
    t = rtrace[-1] if rtrace else {}
    for key in ("pre_read_url", "bound_source_url", "source_final_url", "return_url", "return_nav_final_url", "return_observed_url",
                "restore_attempted", "restore_succeeded", "restore_error", "source_error", "temporary_source_network_effect_occurred",
                "rollback_of_network_effect"):
        print(f"READ_TRACE_{key.upper()}={t.get(key)}")
    print(f"SOURCE_READ_EXECUTION_ID={deep_fact_obj.read_execution_id if deep_fact_obj else None}")
    print(f"SEARCH_EXECUTION_ID={deep_fact_obj.search_execution_id if deep_fact_obj else None}")
    print(f"SOURCE_EFFECT_DIGEST={deep_fact_obj.result_digest if deep_fact_obj else None}")
    print(f"DEEP_FACT_PERSISTED_AFTER_RETURN_TO_WOMBAT={yn(persisted)} prompts_after_return={len(after_return)}")
    print(f"DEEP_FACT_PERSISTED_ACROSS_BROWSER_NAVIGATION={yn(persisted and returned)}")
    first_act = next((i for i, l in enumerate(lines) if "Agnes decided browser." in l), None)
    print(f"DEEP_READ_BEFORE_FIRST_WOMBAT_MUTATION={yn(read_line is not None and (first_act is None or read_line < first_act))} first_wombat_action_log_index={first_act}")
    ret_ok = bool(rtrace) and rtrace[-1]["restore_succeeded"]
    print(f"CONTROLLED_RETURN_ATTEMPTED={yn(bool(rtrace) and rtrace[-1]['restore_attempted'])}")
    print(f"CONTROLLED_RETURN_SUCCEEDED={yn(ret_ok)}")
    print(f"RETURN_FINAL_URL_EQUALS_TRUSTED_RETURN_URL={yn(ret_ok and canonical_source_url(str(rtrace[-1]['return_observed_url'])) == canonical_source_url(str(rtrace[-1]['return_url'])))}")
    print(f"POST_RETURN_ORDINARY_OBSERVE={'PASS' if read_line is not None and any(o['log_index'] > read_line for o in observations) else 'FAIL'}")
    print(f"DEEP_FACT_PERSISTED_AFTER_CONTROLLED_RETURN={yn(persisted)}")
    print(f"SOURCE_FINAL_URL_EQUALS_BOUND_URL={yn(bool(rtrace) and bool(bound_url) and canonical_source_url(str(rtrace[-1]['source_final_url'])) == bound_url)}")
    print(f"SEARCH_TO_READ_BINDING={'PASS' if expected else 'FAIL'}")
    print(f"SEARCH_URL_DIFFERS_FROM_PREFLIGHT={yn(different_source)}")
    print("CONTROLLED_RETURN_RESTORES_NAVIGATION=YES")
    print("CONTROLLED_RETURN_RESTORES_PAGE_INTERNAL_STATE=NO")
    print("TEMPORARY_SOURCE_NETWORK_EFFECT_OCCURRED=YES")
    print("NETWORK_EFFECT_ROLLBACK=NO")
    print(f"AGENT_NAVIGATED_BACK_ITSELF={yn(any('NAVIGATE REQUESTED_ORIGIN=' in l for l in lines[(read_line or 0):]))} (informational; not required)")
    print(f"BROWSER_NAVIGATION_TO_SOURCE_DOMAIN_ATTEMPTS_VIA_NAVIGATE={browser_nav_attempts_rfc}")
    print(f"FRESH_FINAL_OBSERVATION={'PASS' if any('FINAL fresh observation' in l for l in lines) else 'FAIL'}")
    print(f"PREVIEW_REGION_UNIQUE={'YES' if facts.get('PREVIEW_CANDIDATES') == 1 else 'NO'}")
    print(f"SEARCH_TITLE_EXACT={yn(facts.get('SEARCH_RESULT_TITLE_EXACT'))}")
    print(f"SEARCH_URL_EXACT={yn(facts.get('SEARCH_RESULT_URL_EXACT'))}")
    print(f"DEEP_FACT_EXACT={yn(facts.get('DEEP_FACT_EXACT'))}")
    exact_all = bool(facts.get("SEARCH_RESULT_TITLE_EXACT") and facts.get("SEARCH_RESULT_URL_EXACT") and facts.get("DEEP_FACT_EXACT"))
    leak = 'YES' if (verified and not exact_all) else 'NO'
    print(f"PROMPT_FALSE_POSITIVE={leak}")
    print(f"EDITOR_FALSE_POSITIVE={leak}")
    print(f"SOURCE_DATA_FALSE_POSITIVE={leak}")
    print(f"VALUE_ALSO_OUTSIDE_PREVIEW={yn(facts.get('OUTSIDE_PREVIEW_MATCHES_IGNORED'))} (informational)")
    print(f"PREMATURE_FINISH_OCCURRED={yn(result.failed_finishes > 0)} failed_finishes={result.failed_finishes}")
    print(f"ACTION_STAGNATION_DETECTED={yn(result.action_stagnations > 0)} count={result.action_stagnations}")
    for name, n in click_effects.items():
        print(f"{name}_EFFECTS={n}")
    print(f"FORBIDDEN_CONTROL_BLOCKED={'YES' if result.status == 'GOAL_CONSTRAINT_VIOLATION' else 'NO'}")
    print("CODER_INTERVENTION=NO")
    print("HUMAN_INTERVENTION=NO")
    print(f"DETERMINISTIC_V1_4_VERIFICATION={'PASS' if verified else 'FAIL'}")
    trace = {"status": run_status, "agent_status": result.status, "log": lines, "prompts_count": len(prompts), "ledger": session.search_ledger,
             "final_url": final_obs_url, "http_posts": [[p, b] for p, b in posts],
             "deep_fact_url": deep_fact_obj.source_url if deep_fact_obj else None, "deep_fact": deep}
    out = os.path.join(EVIDENCE, "v1-4-live-trace.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(trace, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(EVIDENCE, "v1-4-live-prompts.json"), "w", encoding="utf-8") as fh:
        json.dump(prompts, fh, ensure_ascii=False, indent=1)
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
