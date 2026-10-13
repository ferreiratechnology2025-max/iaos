"""IAOS V1.4 real deep-read PREFLIGHT (no Agnes, no Wombat, no agent loop).

Real chain: governed Brave web.search -> SearchSourceBinding -> governed read_source(1) -> real OpenBot visible_text -> DeepReadFact.
Usage (repo root):  python -m scripts.agent_wombat_v14_preflight --computer-env <f> --agnes-env <f>   (agnes-env only supplies the Brave key)
Any failed condition => PREFLIGHT=ABORT. Nothing is adapted: no policy change, no larger text limit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from application.agent_loop import build_prompt
from application.agent_runtime import GovernedComputerSession
from application.computer_runtime import ComputerPageObservation, OpenBotComputerAdapter
from application.source_facts import SearchConfig, SourceFactRejected, SourceFactStore
from application.wombat_v14_verifier import deep_preflight
from cognitive.agent_contracts import AgentGoal
from governance.aep_executors import BraveSearchExecutor
from scripts.agent_todomvc_e2e import AGNES_KEYS, COMPUTER_KEYS, read_env

QUERY = "RFC 2616 HTTP/1.1"
SEARCH = SearchConfig(("www.rfc-editor.org",), result_limit=1)
ALLOWED = ("https://websitewombat.app",)
GOAL = AgentGoal(
    goal_id="deep-read-e2e-wombat",
    objective=(
        "Pesquise na fonte pública autorizada sobre RFC 2616 e HTTP/1.1.\n\n"
        "Leia profundamente a fonte encontrada.\n\n"
        "Depois crie no Website Wombat uma página simples que apresente:\n"
        "- o título exato do resultado encontrado;\n"
        "- a URL exata da fonte encontrada;\n"
        "- a frase exata da fonte que informa quais RFCs atualizaram o documento.\n\n"
        "Não publique, não faça download e não crie links de compartilhamento."),
    constraints=(),
    max_steps=60,
)


class RecordingAdapter(OpenBotComputerAdapter):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = []

    def _request_json(self, method, path, body=None, timeout=None):
        self.calls.append((method, path, dict(body) if isinstance(body, dict) else body))
        return super()._request_json(method, path, body, timeout) if timeout is not None else super()._request_json(method, path, body)


def one_chain(port: RecordingAdapter, key: str) -> dict:
    os.environ["BRAVE_SEARCH_API_KEY"] = key
    executor = BraveSearchExecutor()
    session = GovernedComputerSession(port, allowed_origins=ALLOWED, search=SEARCH, search_executor=executor, allow_read=True)
    start = len(port.calls)
    out = {"search_ok": False}
    s = session.search("pf-search", QUERY)
    out.update(search_code=s.code, search_ok=s.ok)
    store = SourceFactStore()
    if not s.ok:
        return out
    store.admit(s.candidate)
    item = dict(s.candidate.item)
    out["search_item"] = {k: item.get(k, "") for k in ("title", "url", "description")}
    b = session.bindings[0]
    r = session.read_source("pf-read", b.search_execution_id)
    out.update(read_code=r.code, read_ok=r.ok, binding_url=b.canonical_url)
    deep = None
    if r.ok:
        c = r.candidate
        out.update(final_url=c.final_url, challenge=c.challenge, truncated=c.truncated, text_len=len(c.visible_text), text=c.visible_text,
                   text_sha=hashlib.sha256(c.visible_text.encode()).hexdigest())
        try:
            deep = store.admit_deep_read(c, session.deep_read_is_correlated)
        except SourceFactRejected as exc:
            out["admit_code"] = exc.code
    out["admitted"] = deep is not None
    out["render"] = store.render()
    out["fact"] = deep
    calls = port.calls[start:]
    out["posts"] = [(p, bd) for m, p, bd in calls if m == "POST"]
    out["control_requests"] = [p for m, p, _ in calls if p.startswith("/control/request")]
    out["human_events"] = sum(1 for e in session.evidence.events() if e.get("action") == "browser.request_human")
    out["search_results"] = len(session.search_ledger)
    return out


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
    port = RecordingAdapter(cc["IAOS_COMPUTER_URL"], cc["IAOS_COMPUTER_BOT_ID"], cc["IAOS_COMPUTER_TOKEN"])
    ctl = port.read_control_state(None)
    print(f"HOLDER_BEFORE={ctl.holder} REQUESTED_BEFORE={ctl.requested}")
    runs = [one_chain(port, bk["BRAVE_SEARCH_API_KEY"]) for _ in range(2)]
    ctl2 = port.read_control_state(None)
    print(f"HOLDER_AFTER={ctl2.holder} REQUESTED_AFTER={ctl2.requested}")
    first_prompt = build_prompt(GOAL, ComputerPageObservation("about:blank", "", "s", 0, (), ""), 1, None, ALLOWED, None, "", True, True)
    reports = []
    for i, r in enumerate(runs, 1):
        if not (r.get("search_ok") and r.get("read_ok")):
            print(f"RUN{i} FAILED {r.get('search_code')} {r.get('read_code')}")
            return 1
        takeovers = len(r["control_requests"]) + r["human_events"]
        rep = deep_preflight(result_count=r["search_results"], search_url=r["search_item"]["url"], bound_url=r["binding_url"],
                             final_url=r["final_url"], challenge=r["challenge"], takeover_requests=takeovers, truncated=r["truncated"],
                             visible_text=r["text"], read_admitted=r["admitted"], fact_created=r["fact"] is not None,
                             search_item=r["search_item"], goal_text=GOAL.objective, first_prompt=first_prompt, first_observation_text="")
        rep["REQUEST_HELP_FALSE"] = "PASS" if (r["posts"] == [("/snapshot", {"requestHelp": False}),
                                                               ("/navigate", {"url": r["binding_url"], "requestHelp": False})]) else "FAIL"
        if rep["REQUEST_HELP_FALSE"] == "FAIL":
            rep["PREFLIGHT"] = "ABORT"
            rep["PREFLIGHT_FAILING"].append("REQUEST_HELP_FALSE")
        from application.wombat_v14_verifier import extract_deep_fact
        rep["DEEP_FACT_IN_COGNITION_SOURCE_DATA"] = "YES" if (r["fact"] is not None and extract_deep_fact(r["fact"].visible_text_excerpt) == rep["DEEP_FACT"] and rep["DEEP_FACT"] in " ".join(r["render"].replace("| ", " ").split())) else "NO"
        if rep["DEEP_FACT_IN_COGNITION_SOURCE_DATA"] == "NO":
            rep["PREFLIGHT"] = "ABORT"
            rep["PREFLIGHT_FAILING"].append("DEEP_FACT_IN_COGNITION_SOURCE_DATA")
        reports.append(rep)
    a, b = runs
    stable = (a["search_item"]["url"] == b["search_item"]["url"] and a["search_item"]["title"] == b["search_item"]["title"]
              and reports[0]["DEEP_FACT"] == reports[1]["DEEP_FACT"] and a["final_url"] == b["final_url"])
    rep = reports[0]
    ok = all(x["PREFLIGHT"] == "OK" for x in reports) and stable and ctl2.holder == "bot" and not ctl2.requested
    print(f"SEARCH_QUERY={QUERY}")
    print(f"SEARCH_RESULT_URL={a['search_item']['url']}")
    print(f"SEARCH_RESULT_TITLE={a['search_item']['title']}")
    print(f"READ_FINAL_URL={a['final_url']}")
    for k in ("SEARCH_RESULT_COUNT", "BOUND_URL_EQUALS_SEARCH_URL", "FINAL_CANONICAL_URL_EQUALS_BOUND", "REQUEST_HELP_FALSE", "CHALLENGE_PRESENT",
              "TAKEOVER_REQUESTS", "VISIBLE_TEXT_TRUNCATED", "READ_RESULT_ADMITTED", "DEEP_READ_FACT_CREATED", "DEEP_FACT_EXTRACTION",
              "DEEP_FACT_IN_SEARCH_SNIPPET", "DEEP_FACT_IN_VISIBLE_TEXT", "DEEP_FACT_NOT_IN_SEARCH_TITLE", "DEEP_FACT_NOT_IN_SEARCH_URL",
              "DEEP_FACT_NOT_IN_SEARCH_DESCRIPTION", "DEEP_FACT_NOT_IN_GOAL", "DEEP_FACT_NOT_IN_INITIAL_PROMPT", "DEEP_FACT_TRUNCATED_AWAY", "DEEP_FACT_IN_COGNITION_SOURCE_DATA"):
        print(f"{k}={rep.get(k)}")
    print("DEEP_FACT_NOT_IN_INITIAL_OBSERVATION=checked at run time by the live harness (Wombat is not touched in this preflight)")
    print(f"DEEP_FACT={rep['DEEP_FACT']}")
    print(f"TEXT_LEN={a['text_len']} STABLE_ACROSS_2_CHAINS={'YES' if stable else 'NO'}")
    print(f"PREFLIGHT={'OK' if ok else 'ABORT'} failing={[x['PREFLIGHT_FAILING'] for x in reports]}")
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "evidence", "v1-4-deep-read-preflight.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"query": QUERY, "reports": reports, "stable": stable, "ok": ok,
                   "runs": [{k: v for k, v in r.items() if k not in ("text", "fact", "search_item", "render")} | {"search_item": r["search_item"]} for r in runs]},
                  fh, ensure_ascii=False, indent=1, default=str)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
