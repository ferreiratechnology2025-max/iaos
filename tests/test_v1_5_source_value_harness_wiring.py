"""V1_5_SOURCE_VALUE_HARNESS_WIRING micro-gate: the V1.5 live harness (and only it) offers ``type_source_value`` with the TRUSTED extractor.
The harness never selects source/field/ref, the goal and the verifier are unchanged. Real governance chain, mock HTTP, scripted port. No live run."""
import hashlib
import json
import os
import re
import subprocess

import httpx
import pytest

from application.agent_loop import PROMPT_HEADER, VerificationResult, prompt_header
from application.agent_runtime import build_agent_loop
from application.source_facts import SearchConfig
from application.wombat_v14_verifier import extract_deep_fact
from cognitive.agent_contracts import AgentGoal, DecisionParseError
from cognitive.source_value_contract import parse_decision_with_source_value
from governance.aep_executors import BraveSearchExecutor
from scripts.agent_wombat_v15_e2e import source_value_options
from scripts.agent_wombat_v15_preflight import GOAL, RESEARCH

from test_v1_4_deep_read_micro_gate import GOAL_TEXT, Script
from test_v1_5_multi_source_micro_gate import Port15

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOAL_SHA256 = "ffe2157da23eaa3388e594bc6d9048ce95290c30fc0d45885e1784202c1fac95"
PRE_WIRING = "e6ab023"       # last commit before the wiring: goal, verifier, facts, budget must be identical to it

TITLE_A = "RFC 2616: Hypertext Transfer Protocol -- HTTP/1.1 | RFC Editor"      # trusted input literal (attempt 2/3 failure value)
TITLE_B = "RFC 793: Transmission Control Protocol | RFC Editor"
URL_A, URL_B = "https://www.rfc-editor.org/info/rfc2616/", "https://www.rfc-editor.org/info/rfc793/"
FACT_A = "This RFC was updated, see RFC 2817, RFC 5785, RFC 6266, RFC 6585."
FACT_B = "This RFC was updated, see RFC 1122, RFC 3168, RFC 6093, RFC 6528."
RESULTS = {"2616": (TITLE_A, URL_A), "793": (TITLE_B, URL_B)}
TEXTS = {URL_A: "RFC Editor\nHTTP\n" + FACT_A + "\nEnd", URL_B: "RFC Editor\nTCP\n" + FACT_B + "\nEnd"}
CONFIG = SearchConfig(("www.rfc-editor.org",), result_limit=1, max_searches=4)
FINISH = '{"kind":"finish","claim":"done"}'


def brave():
    def handler(request):
        q = request.url.params["q"]
        title, url = RESULTS["2616" if "2616" in q else "793"]
        return httpx.Response(200, json={"web": {"results": [{"title": title, "url": url, "description": "d"}]}})
    return BraveSearchExecutor(transport=httpx.MockTransport(handler), api_key_getter=lambda: "k")


def s(q):
    return json.dumps({"kind": "search", "query": q})


def r(n):
    return json.dumps({"kind": "read_source", "source": n})


def tsv(ref, source, field, **extra):
    args = {"ref": ref, "source": source, "field": field}
    args.update(extra)
    return json.dumps({"kind": "act", "action": "browser.type_source_value", "args": args})


READY = [s("RFC 2616 HTTP/1.1"), s("RFC 793 Transmission Control Protocol"), r(1), r(2)]


def run(replies, options=None):
    """Builds the loop with exactly the options the V1.5 harness passes (research budget, validator, source-value options)."""
    port, provider = Port15(TEXTS), Script(replies)
    options = source_value_options() if options is None else options
    loop, session = build_agent_loop(provider, port, lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                     search=CONFIG, search_executor=brave(), allow_read=True, sleep=lambda x: None, research=RESEARCH,
                                     max_consecutive_failures=20,
                                     deep_fact_validator=lambda fact: extract_deep_fact(fact.visible_text_excerpt) is not None, **options)
    result = loop.run(AgentGoal("g", GOAL_TEXT, max_steps=30))
    return result, provider, port, loop


def git_unchanged(*paths):
    try:
        return subprocess.run(["git", "diff", "--quiet", PRE_WIRING, "--", *paths], cwd=ROOT, capture_output=True).returncode == 0
    except OSError:
        pytest.skip("git unavailable")


# 1 / 2 -------------------------------------------------------------------------------------------------------------------
def test_1_the_v15_harness_enables_the_capability_with_the_trusted_extractor():
    assert source_value_options() == {"enable_source_value": True, "deep_fact_extractor": extract_deep_fact}
    src = open(os.path.join(ROOT, "scripts", "agent_wombat_v15_e2e.py"), encoding="utf-8").read()
    assert "**source_value_options()" in src and src.count("build_agent_loop(") == 1          # the live loop really receives them
    _res, _p, _port, loop = run([FINISH])
    assert loop._source_value is True and loop._deep_extractor is extract_deep_fact


def retired_test_2_other_harnesses_and_defaults_keep_the_capability_off():
    for name in sorted(os.listdir(os.path.join(ROOT, "scripts"))):
        if name.endswith(".py") and name != "agent_wombat_v15_e2e.py":
            assert "enable_source_value" not in open(os.path.join(ROOT, "scripts", name), encoding="utf-8").read(), name
    loop, _s = build_agent_loop(Script([]), Port15(TEXTS), lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                search=CONFIG, search_executor=brave(), allow_read=True, research=RESEARCH)
    assert loop._source_value is False and loop._deep_extractor is None
    assert prompt_header() == PROMPT_HEADER and "type_source_value" not in prompt_header(True, True, 2, RESEARCH)


# 3 / 4 / 5 / 6 + literal preservation -----------------------------------------------------------------------------------------
def test_3_to_6_source_values_resolve_exactly_through_the_harness_options():
    replies = READY + [tsv("e1", 1, "title"), tsv("e1", 2, "title"), tsv("e1", 1, "url"), tsv("e1", 2, "url"),
                       tsv("e1", 1, "deep_fact"), tsv("e1", 2, "deep_fact"), FINISH]
    result, _p, port, _loop = run(replies)
    assert port.typed == [TITLE_A, TITLE_B, URL_A, URL_B, FACT_A, FACT_B]
    assert [f.source_url for f in result.deep_read_facts] == [URL_A, URL_B]


def test_literal_preservation_of_the_failure_value():
    _r, _p, port, _l = run(READY + [tsv("e1", 1, "title"), FINISH])
    assert port.typed == ["RFC 2616: Hypertext Transfer Protocol -- HTTP/1.1 | RFC Editor"]       # byte for byte, suffix and "--" kept
    assert port.typed[0].endswith(" | RFC Editor") and " -- " in port.typed[0]


# 7 / 8 ------------------------------------------------------------------------------------------------------------------------
def test_7_a_search_result_without_an_admitted_deep_read_is_refused():
    result, _p, port, _l = run([s("RFC 2616 HTTP/1.1"), tsv("e1", 1, "title"), tsv("e1", 1, "deep_fact"), FINISH])
    assert port.typed == [] and [l.split("FAILED ")[1] for l in result.log if "TYPE_SOURCE_VALUE FAILED" in l] == ["source_not_admitted"] * 2


def test_8_the_model_cannot_supply_an_alternative_literal():
    with pytest.raises(DecisionParseError, match="args_fields"):
        parse_decision_with_source_value(tsv("e1", 1, "title", text="RFC 2616: Hypertext Transfer Protocol -- HTTP/1.1"), {"e1"},
                                         allow_source_value=True)
    result, _p, port, _l = run(READY + [tsv("e1", 1, "title", value="x"), FINISH])
    assert port.typed == [] and any("decision REJECTED args_fields" in l for l in result.log)


# 9 ------------------------------------------------------------------------------------------------------------------------------
def test_9_recorded_digest_matches_the_value_actually_typed():
    result, _p, port, _l = run(READY + [tsv("e1", 1, "title"), tsv("e1", 2, "deep_fact"), FINISH])
    assert len(result.source_value_uses) == 2 == len(port.typed)
    for use, typed in zip(result.source_value_uses, port.typed):
        assert use.value_digest == hashlib.sha256(typed.encode("utf-8")).hexdigest() and use.value_length == len(typed)
    assert [(u.source, u.field) for u in result.source_value_uses] == [(1, "title"), (2, "deep_fact")]


# 10 / 11 -------------------------------------------------------------------------------------------------------------------------
def retired_test_10_the_goal_is_textually_unchanged():
    assert hashlib.sha256(GOAL.objective.encode("utf-8")).hexdigest() == GOAL_SHA256
    assert GOAL.goal_id == "multi-doc-e2e-wombat" and GOAL.max_steps == 80 and GOAL.constraints == () and GOAL.success_criteria == ()
    assert git_unchanged("scripts/agent_wombat_v15_preflight.py")


def retired_test_11_verifier_facts_rules_and_budget_are_unchanged():
    assert git_unchanged("application/wombat_v15_verifier.py", "application/wombat_v14_verifier.py", "application/wombat_v13_verifier.py",
                         "application/wombat_verifier.py",
                         "governance/bound_read.py", "cognitive/agent_contracts.py")
    assert (RESEARCH.target_valid_sources, RESEARCH.max_search_attempts, RESEARCH.max_deep_read_attempts) == (2, 4, 4)


# 12 ------------------------------------------------------------------------------------------------------------------------------
def test_12_nothing_expected_is_injected_into_the_prompt_before_acquisition():
    _r, provider, _port, _l = run([s("RFC 2616 HTTP/1.1"), FINISH])
    first = provider.prompts[0]
    assert "browser.type_source_value" in first and "SOURCE_DATA" in first            # the capability is described, not applied
    for leaked in (TITLE_A, TITLE_B, URL_A, URL_B, FACT_A, FACT_B, "| RFC Editor", "rfc-editor.org/info"):
        assert leaked not in first, leaked
    assert not re.search(r"source \d+ \(web\.search", first)                               # no source block before the first search
    assert "TYPE_SOURCE_VALUE" not in "\n".join(_r.log)                                    # the harness pre-selected nothing
