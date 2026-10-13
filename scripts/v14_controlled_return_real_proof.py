"""Real-OpenBot proof of V1_4_DEEP_READ_CONTROLLED_RETURN_V1 (no Agnes, no Wombat edits):
Wombat /build -> governed search -> governed read_source(1) (temporary RFC visit) -> Wombat restored by the runtime ->
ordinary observe succeeds -> DeepReadFact persists.  Usage: python -m scripts.v14_controlled_return_real_proof --computer-env <f> --agnes-env <f>"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from application.agent_runtime import GovernedComputerSession
from application.source_facts import SourceFactRejected, SourceFactStore
from application.wombat_v14_verifier import extract_deep_fact
from governance.aep_executors import BraveSearchExecutor
from scripts.agent_todomvc_e2e import COMPUTER_KEYS, read_env
from scripts.agent_wombat_e2e import TARGET
from scripts.agent_wombat_v14_preflight import ALLOWED, QUERY, SEARCH, RecordingAdapter


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--computer-env", required=True)
    p.add_argument("--agnes-env", required=True)
    args = p.parse_args(argv)
    cc = read_env(args.computer_env, COMPUTER_KEYS)
    bk = read_env(args.agnes_env, ("BRAVE_SEARCH_API_KEY",))
    os.environ.update(bk)
    port = RecordingAdapter(cc["IAOS_COMPUTER_URL"], cc["IAOS_COMPUTER_BOT_ID"], cc["IAOS_COMPUTER_TOKEN"])
    ctl = port.read_control_state(None)
    print(f"HOLDER_BEFORE={ctl.holder} REQUESTED_BEFORE={ctl.requested}")
    port.observe()
    port.navigate(TARGET)
    time.sleep(6)
    ex = BraveSearchExecutor()
    session = GovernedComputerSession(port, allowed_origins=ALLOWED, search=SEARCH, search_executor=ex, allow_read=True)
    pre = session.observe("rp-observe-1")
    s = session.search("rp-search", QUERY)
    store = SourceFactStore()
    store.admit(s.candidate)
    start = len(port.calls)
    r = session.read_source("rp-read", session.bindings[0].search_execution_id)
    calls = port.calls[start:]
    print(f"READ_ACTION_STATUS={'SUCCESS' if r.ok else 'FAILED:' + r.code}")
    fact = None
    if r.ok:
        try:
            fact = store.admit_deep_read(r.candidate, session.deep_read_is_correlated)
        except SourceFactRejected as exc:
            print(f"ADMIT_REJECTED={exc.code}")
    post = session.observe("rp-observe-2")                      # the next ORDINARY observation, as the loop would do
    t = session._read_executor.trace[-1]
    posts = [(path, b) for m, path, b in calls if m == "POST"]
    rendered = " ".join(store.render().replace("| ", " ").split())
    deep = extract_deep_fact(fact.visible_text_excerpt) if fact else None
    ctl2 = port.read_control_state(None)
    control_requests = [path for m, path, _ in port.calls if path.startswith("/control/request")]
    out = {
        "PRE_READ_URL": pre.page_url, "BOUND_SOURCE_URL": t["bound_source_url"], "SOURCE_FINAL_URL": t["source_final_url"],
        "RETURN_URL": t["return_url"], "RETURN_FINAL_URL": t["return_nav_final_url"], "RETURN_OBSERVED_URL": t["return_observed_url"],
        "NEXT_ORDINARY_OBSERVE_URL": post.page_url, "NEXT_ORDINARY_OBSERVE_ELEMENTS": len(post.elements),
        "SOURCE_READ_EXECUTION_ID": fact.read_execution_id if fact else None, "SEARCH_EXECUTION_ID": session.bindings[0].search_execution_id,
        "SOURCE_EFFECT_DIGEST": fact.result_digest if fact else None,
        "RESTORE_ATTEMPTED": t["restore_attempted"], "RESTORE_SUCCEEDED": t["restore_succeeded"],
        "TEMPORARY_SOURCE_NETWORK_EFFECT_OCCURRED": t["temporary_source_network_effect_occurred"], "ROLLBACK_OF_NETWORK_EFFECT": t["rollback_of_network_effect"],
        "POST_SEQUENCE": [path for path, _ in posts],
        "REQUEST_HELP_FALSE_ALL_CALLS": all(isinstance(b, dict) and b.get("requestHelp") is False for _p, b in posts),
        "TAKEOVER_REQUESTS": len(control_requests), "HOLDER_AFTER": ctl2.holder, "PENDING_TAKEOVER_AFTER": ctl2.requested,
        "VISIBLE_TEXT_TRUNCATED": fact.truncated if fact else None, "DEEP_FACT": deep,
        "DEEP_FACT_PERSISTS_IN_SOURCE_DATA_AFTER_RETURN": bool(deep) and deep in rendered,
    }
    ok = (r.ok and fact is not None and out["RESTORE_SUCCEEDED"] and out["RETURN_OBSERVED_URL"].rstrip("/") == TARGET and post.page_url.rstrip("/") == TARGET
          and out["REQUEST_HELP_FALSE_ALL_CALLS"] and out["TAKEOVER_REQUESTS"] == 0 and out["DEEP_FACT_PERSISTS_IN_SOURCE_DATA_AFTER_RETURN"]
          and len(post.elements) > 0 and ctl2.holder == "bot" and not ctl2.requested)
    for k, v in out.items():
        print(f"{k}={v}")
    print(f"CONTROLLED_RETURN_REAL_PROOF={'PASS' if ok else 'FAIL'}")
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "evidence", "v1-4-controlled-return-real-proof.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"ok": ok, **out}, fh, ensure_ascii=False, indent=1, default=str)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
