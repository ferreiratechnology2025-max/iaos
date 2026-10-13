"""IAOS V1.5 real TWO-DOCUMENT preflight (no Agnes, no agent loop, no Wombat edits).

Real chains in ONE governed session: Brave web.search A, web.search B -> bindings -> governed read_source A -> controlled return ->
ordinary observe -> governed read_source B -> controlled return -> ordinary observe. Wombat /build is the page the runtime returns to.
Targets MULTI-DOCUMENT research only (INDEPENDENT_PUBLISHER_RESEARCH is NOT a target; both documents come from the same publisher).
Any failed condition => PREFLIGHT=ABORT; a wrong Brave result => SOURCE_PREFLIGHT_FAILED. Nothing is adapted, no fallback URL is used.
Usage (repo root):  python -m scripts.agent_wombat_v15_preflight --computer-env <f> --agnes-env <f>   (agnes-env only supplies the Brave key)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import replace
from urllib.parse import urlparse

from application.agent_loop import build_prompt
from application.agent_runtime import GovernedComputerSession
from application.source_facts import ResearchBudget, SearchConfig, SourceFactRejected, SourceFactStore
from application.wombat_v14_verifier import check_deep_fact_provenance, deep_preflight, extract_deep_fact
from application.wombat_v15_verifier import chain_expectations, make_v15_verifier
from cognitive.agent_contracts import AgentGoal
from governance.aep_executors import BraveSearchExecutor
from governance.bound_read import canonical_source_url
from scripts.agent_todomvc_e2e import COMPUTER_KEYS, read_env
from scripts.agent_wombat_e2e import TARGET
from scripts.agent_wombat_v14_preflight import RecordingAdapter

QUERY_A = "RFC 2616 HTTP/1.1"
QUERY_B = "RFC 793 Transmission Control Protocol"
WANTED = (2616, 793)                                    # document identity, decided deterministically from the LIVE result
RESEARCH = ResearchBudget(target_valid_sources=2, max_search_attempts=4, max_deep_read_attempts=4)   # budget v2: attempts != valid sources
SEARCH = SearchConfig(("www.rfc-editor.org",), result_limit=1, max_searches=RESEARCH.max_search_attempts)
MAX_DEEP_READS = RESEARCH.target_valid_sources
ALLOWED = ("https://websitewombat.app",)
WOMBAT = "https://websitewombat.app"
GOAL = AgentGoal(
    goal_id="multi-doc-e2e-wombat",
    objective=(
        "Pesquise na fonte pública autorizada sobre RFC 2616 / HTTP/1.1 e RFC 793 / Transmission Control Protocol.\n\n"
        "Leia profundamente as duas fontes encontradas.\n\n"
        "Depois crie no Website Wombat uma página simples que apresente, para cada documento:\n"
        "- o título exato do resultado encontrado;\n"
        "- a URL exata da fonte encontrada;\n"
        "- a frase exata que informa quais RFCs atualizaram o documento.\n\n"
        "Não publique, não faça download e não crie links de compartilhamento."),
    constraints=(),
    max_steps=80,        # V1.5 harness choice: twice the research work of V1.4 (60); recorded, not tuned to a result
)
EVIDENCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "evidence")


def yn(value) -> str:
    return "YES" if value else "NO"


def norm(text: str) -> str:
    return " ".join(str(text).split())


def has(text: str, value: str) -> bool:
    return bool(value) and norm(value).casefold() in norm(text).casefold()


def identify_rfc(item: dict) -> int | None:
    """Deterministic document identity of a LIVE search result: the RFC number must agree in the canonical path AND the title."""
    try:
        path = urlparse(canonical_source_url(str(item.get("url", "")))).path
    except ValueError:
        return None
    m_url = re.fullmatch(r"/info/rfc(\d+)/?", path)
    m_title = re.match(r"\s*RFC\s+(\d+)\b", str(item.get("title", "")))
    if m_url and m_title and m_url.group(1) == m_title.group(1):
        return int(m_url.group(1))
    return None


def fake_wombat_page(*texts: str) -> str:
    """Minimal Wombat-shaped aria snapshot (chrome + ONE preview iframe) used only to exercise the real V1.5 verifier on real evidence."""
    body = "\n".join(f'                - paragraph [ref=q{i}]: "{t}"' for i, t in enumerate(texts))
    return ('- generic [active] [ref=f1e1]:\n    - generic [ref=f1e2]:\n        - button "My sites" [ref=f1e5]\n'
            '        - button "Edit link" [ref=f1e16]\n        - button "Copy link" [ref=f1e17]\n        - button "Download" [ref=f1e18]\n'
            '    - generic [ref=f1e19]:\n        - generic [ref=f1e20]:\n            - textbox "Manifest JSON" [ref=f1e30]: ""\n'
            '        - iframe [ref=f1e153]:\n            - generic [ref=p1]:\n' + body + '\n'
            '    - generic [ref=f1e155]:\n        - textbox "e.g. A painting company in Philadelphia" [ref=f1e185]: ""\n')


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--computer-env", required=True)
    p.add_argument("--agnes-env", required=True)
    args = p.parse_args(argv)
    cc = read_env(args.computer_env, COMPUTER_KEYS)
    bk = read_env(args.agnes_env, ("BRAVE_SEARCH_API_KEY",))
    if len(cc) != len(COMPUTER_KEYS) or "BRAVE_SEARCH_API_KEY" not in bk:
        print("CONFIG_INCOMPLETE")
        return 2
    os.environ["BRAVE_SEARCH_API_KEY"] = bk["BRAVE_SEARCH_API_KEY"]
    port = RecordingAdapter(cc["IAOS_COMPUTER_URL"], cc["IAOS_COMPUTER_BOT_ID"], cc["IAOS_COMPUTER_TOKEN"])
    ctl = port.read_control_state(None)
    print(f"HOLDER_BEFORE={ctl.holder} REQUESTED_BEFORE={ctl.requested}")
    if ctl.holder != "bot" or ctl.requested:
        print("PRECONDITION=FAIL")
        return 3
    port.observe()
    port.navigate(TARGET)
    time.sleep(6)
    executor = BraveSearchExecutor()
    session = GovernedComputerSession(port, allowed_origins=ALLOWED, search=SEARCH, search_executor=executor, allow_read=True)
    initial = session.observe("pf-o0")
    store = SourceFactStore(RESEARCH.target_valid_sources, RESEARCH.max_search_facts, RESEARCH.max_source_context_chars)
    abort: list[str] = []
    rec: dict = {"queries": [QUERY_A, QUERY_B], "initial_url": initial.page_url}
    print(f"INITIAL_PAGE={initial.page_url}")
    if not initial.page_url.startswith(WOMBAT):
        print("PREFLIGHT=ABORT failing=['WOMBAT_NOT_THE_RETURN_PAGE']")
        return 1

    # --- searches ---------------------------------------------------------------------------------------------------------
    items: list[dict] = []
    for tag, query in (("A", QUERY_A), ("B", QUERY_B)):
        s = session.search(f"pf-s{tag}", query)
        print(f"SEARCH_{tag}_EXECUTED={yn(s.ok)} code={s.code}")
        if not s.ok:
            print(f"SOURCE_PREFLIGHT_FAILED search_{tag}={s.code}")
            return 1
        store.admit(s.candidate)
        items.append({k: dict(s.candidate.item).get(k, "") for k in ("title", "url", "description")})
    ident = [identify_rfc(i) for i in items]
    for tag, i, doc in zip("AB", items, ident):
        print(f"SEARCH_{tag}_TITLE={i['title']}")
        print(f"SEARCH_{tag}_URL={i['url']}")
    print(f"SOURCE_RFC2616_IDENTIFIED={yn(ident[0] == 2616)}")
    print(f"SOURCE_RFC793_IDENTIFIED={yn(ident[1] == 793)}")
    rec.update(items=items, identified=ident)
    if tuple(ident) != WANTED:
        print(f"SOURCE_PREFLIGHT_FAILED identified={ident} wanted={list(WANTED)} (no fallback URL is used)")
        _dump(rec)
        return 1
    ledger = session.search_ledger
    print(f"SEARCH_RESULT_COUNT_A={sum(1 for e in ledger if e['url'] == items[0]['url'])} SEARCH_RESULT_COUNT_B={sum(1 for e in ledger if e['url'] == items[1]['url'])}"
          f" LEDGER={len(ledger)} BINDINGS={len(session.bindings)}")

    first_prompt = build_prompt(GOAL, initial, 1, None, ALLOWED, None, "", True, True, MAX_DEEP_READS, RESEARCH)

    # --- reads ------------------------------------------------------------------------------------------------------------
    chains = []
    for idx, tag in enumerate("AB"):
        binding = session.bindings[idx]
        start = len(port.calls)
        r = session.read_source(f"pf-r{tag}", binding.search_execution_id)
        calls = port.calls[start:]
        out = {"tag": tag, "read_code": r.code, "read_ok": r.ok, "binding_url": binding.canonical_url}
        deep = None
        if r.ok:
            c = r.candidate
            out.update(final_url=c.final_url, challenge=c.challenge, truncated=c.truncated, text_len=len(c.visible_text),
                       text_sha=hashlib.sha256(c.visible_text.encode()).hexdigest(), text=c.visible_text)
            try:
                deep = store.admit_deep_read(c, session.deep_read_is_correlated)
            except SourceFactRejected as exc:
                out["admit_code"] = exc.code
        out["fact"] = deep
        out["posts"] = [(pth, bd) for m, pth, bd in calls if m == "POST"]
        out["control_requests"] = [pth for m, pth, _ in calls if pth.startswith("/control/request")]
        trace = session._read_executor.trace[-1] if session._read_executor.trace else {}
        out["trace"] = {k: trace.get(k) for k in ("pre_read_url", "bound_source_url", "source_final_url", "return_url", "return_nav_final_url",
                                                   "return_observed_url", "restore_attempted", "restore_succeeded", "restore_error", "source_error")}
        try:
            post = session.observe(f"pf-o{tag}")
            out["post_observe_url"], out["post_observe_ok"] = post.page_url, post.page_url.startswith(WOMBAT)
        except Exception as exc:
            out["post_observe_url"], out["post_observe_ok"] = f"FAILED:{type(exc).__name__}", False
        out["render_after"] = store.render()
        chains.append(out)
        print(f"READ_{tag}_EXECUTED={yn(r.ok)} code={r.code} admitted={yn(deep is not None)}")
        if not (r.ok and deep is not None):
            print(f"PREFLIGHT=ABORT failing=['READ_{tag}']")
            rec["chains"] = _strip(chains)
            _dump(rec)
            return 1

    # --- evaluation --------------------------------------------------------------------------------------------------------
    ctl2 = port.read_control_state(None)
    print(f"HOLDER_AFTER={ctl2.holder} REQUESTED_AFTER={ctl2.requested}")
    events = session.evidence.events()
    reports = []
    for idx, (tag, out) in enumerate(zip("AB", chains)):
        item = items[idx]
        takeovers = len(out["control_requests"]) + sum(1 for e in events if e.get("action") == "browser.request_human")
        rep = deep_preflight(result_count=1, search_url=item["url"], bound_url=out["binding_url"], final_url=out["final_url"],
                             challenge=out["challenge"], takeover_requests=takeovers, truncated=out["truncated"], visible_text=out["text"],
                             read_admitted=out["fact"] is not None, fact_created=out["fact"] is not None, search_item=item,
                             goal_text=GOAL.objective, first_prompt=first_prompt, first_observation_text=initial.aria_snapshot)
        deep = rep["DEEP_FACT"]
        other = items[1 - idx]
        rep["DEEP_FACT_NOT_IN_OTHER_SNIPPET"] = "YES" if deep and not any(has(other[k], deep) for k in ("title", "url", "description")) else "NO"
        rep["DEEP_FACT_IN_PERSISTED_EXCERPT"] = "YES" if deep and extract_deep_fact(out["fact"].visible_text_excerpt) == deep else "NO"
        rep["REQUEST_HELP_FALSE"] = "PASS" if out["posts"] and all(bd.get("requestHelp") is False for pth, bd in out["posts"]
                                                                    if pth in ("/snapshot", "/navigate") and isinstance(bd, dict)) else "FAIL"
        t = out["trace"]
        rep["CONTROLLED_RETURN"] = "PASS" if (t.get("restore_attempted") and t.get("restore_succeeded") and str(t.get("return_observed_url", "")).startswith(WOMBAT)
                                              and canonical_source_url(str(t["source_final_url"])) == out["binding_url"]) else "FAIL"
        rep["POST_RETURN_OBSERVE"] = "PASS" if out["post_observe_ok"] else "FAIL"
        for key in ("REQUEST_HELP_FALSE", "CONTROLLED_RETURN", "POST_RETURN_OBSERVE"):
            if rep[key] == "FAIL":
                rep["PREFLIGHT"] = "ABORT"
                rep["PREFLIGHT_FAILING"].append(key)
        for key in ("DEEP_FACT_NOT_IN_OTHER_SNIPPET", "DEEP_FACT_IN_PERSISTED_EXCERPT"):
            if rep[key] == "NO":
                rep["PREFLIGHT"] = "ABORT"
                rep["PREFLIGHT_FAILING"].append(key)
        reports.append(rep)
    a, b = reports
    fa, fb = (c["fact"] for c in chains)
    canon = [canonical_source_url(i["url"]) for i in items]
    distinct = {"DISTINCT_SEARCH_EXECUTIONS": fa.search_execution_id != fb.search_execution_id,
                "DISTINCT_READ_EXECUTIONS": fa.read_execution_id != fb.read_execution_id,
                "DISTINCT_CANONICAL_URLS": canon[0] != canon[1],
                "DISTINCT_TITLES": norm(items[0]["title"]) != norm(items[1]["title"]),
                "DISTINCT_DEEP_FACTS": a["DEEP_FACT"] is not None and a["DEEP_FACT"] != b["DEEP_FACT"]}
    # persistence: fact A is still the SAME admitted object after B, and SOURCE_DATA (what cognition sees) holds both, each with its own provenance
    final_render = store.render()
    flat = norm(final_render.replace("| ", " "))
    persisted_a = store.deep_facts()[0] == fa and chains[1]["render_after"].count("deep read of source") == 2 and a["DEEP_FACT"] in flat
    two_chain = (f"deep read of source 1 (search_bound_deep_read execution={fa.read_execution_id})" in final_render
                 and f"deep read of source 2 (search_bound_deep_read execution={fb.read_execution_id})" in final_render
                 and b["DEEP_FACT"] in flat)
    # real-evidence correlation of the V1.5 verifier + its rejections
    args = (lambda: session.search_ledger, lambda: events, lambda: tuple(executor._effects), lambda: session.bindings,
            lambda: store.deep_facts(), lambda: session._read_executor.effects())
    verify = make_v15_verifier(*args)
    from application.computer_runtime import ComputerPageObservation
    obs = lambda aria: ComputerPageObservation(WOMBAT + "/build", "w", "s", 1, (), aria)
    six = (items[0]["title"], items[0]["url"], a["DEEP_FACT"], items[1]["title"], items[1]["url"], b["DEEP_FACT"])
    real_ok = verify(obs(fake_wombat_page(*six)))
    missing_b = verify(obs(fake_wombat_page(*six[:3])))
    ledger_missing = make_v15_verifier(lambda: session.search_ledger[:1], *args[1:])(obs(fake_wombat_page(*six)))
    swapped_facts = [replace(fb, search_execution_id=fa.search_execution_id, source_url=fa.source_url),
                     replace(fa, search_execution_id=fb.search_execution_id, source_url=fb.source_url)]
    swap = make_v15_verifier(args[0], args[1], args[2], args[3], lambda: swapped_facts, args[5])(obs(fake_wombat_page(*six)))
    dup_ledger = [session.search_ledger[0], {**session.search_ledger[1], "url": session.search_ledger[0]["url"]}]
    dup_bind = [session.bindings[0], replace(session.bindings[1], canonical_url=session.bindings[0].canonical_url)]
    dup_facts = [fa, replace(fb, source_url=fa.source_url)]
    dup = make_v15_verifier(lambda: dup_ledger, args[1], args[2], lambda: dup_bind, lambda: dup_facts, args[5])(obs(fake_wombat_page(*six)))
    chains_real, _fail = chain_expectations(session.search_ledger, events, tuple(executor._effects), session.bindings, store.deep_facts(),
                                            session._read_executor.effects())
    corr = all(c[2] is not None for c in chains_real)
    verifier_correlation = real_ok.verified and corr and not missing_b.verified and not ledger_missing.verified and not swap.verified and not dup.verified
    goal_clean = not any(has(GOAL.objective, v) for v in (a["DEEP_FACT"], b["DEEP_FACT"]))
    init_clean = not any(has(initial.aria_snapshot, v) for v in six)
    ok = (all(x["PREFLIGHT"] == "OK" for x in reports) and all(distinct.values()) and persisted_a and two_chain and verifier_correlation
          and goal_clean and init_clean and ctl2.holder == "bot" and not ctl2.requested)

    print("--- V1_5_REAL_PREFLIGHT")
    for tag, q, i, rep, fact in (("A", QUERY_A, items[0], a, fa), ("B", QUERY_B, items[1], b, fb)):
        print(f"SOURCE_{tag}_QUERY={q}")
        print(f"SOURCE_{tag}_TITLE={i['title']}")
        print(f"SOURCE_{tag}_URL={i['url']}")
        print(f"SOURCE_{tag}_DEEP_FACT={rep['DEEP_FACT']}")
        print(f"SOURCE_{tag}_SEARCH_EXECUTION={fact.search_execution_id} READ_EXECUTION={fact.read_execution_id}")
    for k, v in distinct.items():
        print(f"{k}={yn(v)}")
    print(f"FACT_A_IN_ANY_SEARCH_SNIPPET={yn(a['DEEP_FACT_IN_SEARCH_SNIPPET'] == 'YES' or a['DEEP_FACT_NOT_IN_OTHER_SNIPPET'] == 'NO')}")
    print(f"FACT_B_IN_ANY_SEARCH_SNIPPET={yn(b['DEEP_FACT_IN_SEARCH_SNIPPET'] == 'YES' or b['DEEP_FACT_NOT_IN_OTHER_SNIPPET'] == 'NO')}")
    for tag, rep in (("A", a), ("B", b)):
        for k in ("REQUEST_HELP_FALSE", "CHALLENGE_PRESENT", "TAKEOVER_REQUESTS", "FINAL_CANONICAL_URL_EQUALS_BOUND", "VISIBLE_TEXT_TRUNCATED",
                  "DEEP_FACT_EXTRACTION", "DEEP_FACT_IN_VISIBLE_TEXT", "DEEP_FACT_IN_SEARCH_SNIPPET", "DEEP_FACT_TRUNCATED_AWAY",
                  "DEEP_FACT_IN_PERSISTED_EXCERPT", "DEEP_FACT_NOT_IN_GOAL", "DEEP_FACT_NOT_IN_INITIAL_PROMPT", "PREFLIGHT"):
            print(f"{k}_{tag}={rep.get(k)}")
        print(f"READ_{tag}_CONTROLLED_RETURN={rep['CONTROLLED_RETURN']}")
        print(f"POST_RETURN_OBSERVE_{tag}={rep['POST_RETURN_OBSERVE']}")
    print(f"DEEP_FACT_A_PERSISTED_AFTER_READ_B={yn(persisted_a)}")
    print(f"DEEP_FACT_B_PRESENT={yn(b['DEEP_FACT'] in flat)}")
    print(f"SOURCE_DATA_CONTAINS_TWO_DISTINCT_DEEP_FACTS={yn(two_chain)}")
    print(f"SOURCE_DATA_TWO_CHAIN_PROVENANCE={'PASS' if two_chain else 'FAIL'}")
    print(f"BOTH_DEEP_READS_BEFORE_FIRST_WOMBAT_MUTATION=YES (no Wombat mutation exists in this preflight)")
    print(f"GOAL_CONTAINS_NO_DEEP_FACT={yn(goal_clean)} INITIAL_OBSERVATION_CONTAINS_NONE_OF_THE_SIX_VALUES={yn(init_clean)}")
    print(f"V1_5_VERIFIER_REAL_EVIDENCE_CORRELATION={'PASS' if corr else 'FAIL'} CHAIN_A_CORRELATED={yn(chains_real[0][2] is not None)} CHAIN_B_CORRELATED={yn(chains_real[1][2] is not None)}")
    print(f"V1_5_VERIFIER_ACCEPTS_REAL_SIX_VALUES={yn(real_ok.verified)}")
    print(f"MISSING_CHAIN={'REJECTED' if (not missing_b.verified and not ledger_missing.verified) else 'ACCEPTED'}")
    print(f"CROSS_CHAIN_SWAP={'REJECTED' if not swap.verified else 'ACCEPTED'} reasons={list(swap.reasons)[:2]}")
    print(f"DUPLICATE_URL_CHAIN={'REJECTED' if not dup.verified else 'ACCEPTED'} reasons={list(dup.reasons)[:2]}")
    print(f"V1_5_REAL_VERIFIER_CORRELATION={'PASS' if verifier_correlation else 'FAIL'}")
    print(f"TEXT_LEN_A={chains[0]['text_len']} TEXT_LEN_B={chains[1]['text_len']}")
    failing = [r["PREFLIGHT_FAILING"] for r in reports]
    print(f"PREFLIGHT={'OK' if ok else 'ABORT'} failing={failing} distinct_failing={[k for k, v in distinct.items() if not v]}")
    rec.update(reports=reports, distinct=distinct, ok=ok, persisted_a=persisted_a, two_chain=two_chain, final_render=final_render,
               verifier={"real_ok": real_ok.verified, "missing_b": missing_b.verified, "ledger_missing": ledger_missing.verified,
                         "swap": swap.verified, "dup": dup.verified, "swap_reasons": list(swap.reasons), "dup_reasons": list(dup.reasons)},
               chains=_strip(chains))
    _dump(rec)
    return 0 if ok else 1


def _strip(chains):
    return [{k: v for k, v in c.items() if k not in ("text", "fact", "render_after")} | {"fact": (c["fact"].__dict__ if c.get("fact") else None)}
            for c in chains]


def _dump(rec):
    with open(os.path.join(EVIDENCE, "v1-5-real-preflight.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    sys.exit(main())
