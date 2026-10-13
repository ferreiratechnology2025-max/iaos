"""V1_5_RESEARCH_ACQUISITION_BUDGET_V2 micro-gate: ATTEMPTS are not VALID SOURCES. Real governance chain, mock HTTP, scripted port. No live run.

Replays the observed live failure: search A -> read A (transport ok, deep fact not extractable) -> search B no_results ->
corrected search B must still be allowed -> corrected search A must still fit the attempt budget."""
import hashlib
import json

import httpx
import pytest

from application.agent_loop import VerificationResult, build_prompt, prompt_header
from application.agent_runtime import build_agent_loop
from application.computer_runtime import ComputerPageObservation
from application.source_facts import (MAX_SEARCHES_PER_RUN, MAX_TITLE_CHARS, MAX_URL_CHARS, MAX_DESCRIPTION_CHARS, DeepReadCandidate,
                                      ResearchBudget, SearchConfig, SourceCandidate, SourceFactRejected, SourceFactStore)
from cognitive.agent_contracts import AgentGoal
from governance.aep_executors import BraveSearchExecutor

from test_v1_4_deep_read_micro_gate import GOAL_TEXT, Script
from test_v1_5_multi_source_micro_gate import FROZEN_PROMPTS, Port15

BUDGET = ResearchBudget(2, 4, 4)
CONFIG = SearchConfig(("docs.example.org",), result_limit=1, max_searches=4)
D = "https://docs.example.org/"
ITEMS = {"alpha": ("Alpha Handbook", D + "info/alpha/"), "alphabad": ("Alpha Handbook", D + "rfc/alpha.html"),
         "beta": ("Beta Manual", D + "info/beta/"), "bad1": ("Bad One", D + "rfc/b1.html"), "bad2": ("Bad Two", D + "rfc/b2.html"),
         "bad3": ("Bad Three", D + "rfc/b3.html"), "bad4": ("Bad Four", D + "rfc/b4.html")}
GOODTEXT = "Header\nFACT: the identifier is {n}\nEnd."
BADTEXT = "Header\nno usable sentence here\nEnd."
TEXTS = {D + "info/alpha/": GOODTEXT.format(n=2817), D + "info/beta/": GOODTEXT.format(n=1122)}
TEXTS.update({u: BADTEXT for k, (_t, u) in ITEMS.items() if k.startswith("bad") or k == "alphabad"})


def validator(fact):
    return "FACT:" in fact.visible_text_excerpt


def s(q):
    return json.dumps({"kind": "search", "query": q})


def r(n):
    return json.dumps({"kind": "read_source", "source": n})


FINISH = '{"kind":"finish","claim":"done"}'


def brave():
    def handler(request):
        q = request.url.params["q"]
        if "nothing" in q:
            return httpx.Response(200, json={"web": {"results": []}})
        key = next(k for k in sorted(ITEMS, key=len, reverse=True) if k in q)
        title, url = ITEMS[key]
        return httpx.Response(200, json={"web": {"results": [{"title": title, "url": url, "description": "d " + key}]}})
    return BraveSearchExecutor(transport=httpx.MockTransport(handler), api_key_getter=lambda: "k")


def run(replies, budget=BUDGET, config=CONFIG, same_result=None, **opts):
    port = Port15(TEXTS)
    provider = Script(replies)
    opts.setdefault("max_consecutive_failures", 20)
    loop, session = build_agent_loop(provider, port, lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                     search=config, search_executor=brave(), allow_read=True, sleep=lambda x: None,
                                     research=budget, deep_fact_validator=validator, **opts)
    result = loop.run(AgentGoal("g", GOAL_TEXT, max_steps=40))
    return result, provider, session, port


def codes(result, kind):
    return [l.split(kind + " FAILED ")[1] for l in result.log if kind + " FAILED " in l]


# 1 -------------------------------------------------------------------------------------------------------------------
def test_1_no_results_consumes_an_attempt_not_a_valid_source():
    result, _p, _s, _port = run([s("nothing at all"), FINISH])
    assert result.searches == 1 and codes(result, "SEARCH") == ["no_results"] and result.deep_read_facts == [] and result.source_facts == []


# 2 -------------------------------------------------------------------------------------------------------------------
def test_2_failed_extraction_consumes_an_attempt_not_a_valid_source():
    result, _p, session, port = run([s("alphabad"), r(1), FINISH])
    assert port.reads == [D + "rfc/alpha.html"] and result.deep_reads == 1                      # transport PASS, attempt counted
    assert codes(result, "DEEP_READ") == ["deep_fact_extraction_failed"] and result.deep_read_facts == []   # SOURCE_A_ADMITTED=NO


# 3 + observed sequence (5) -------------------------------------------------------------------------------------------
def test_3_observed_failure_sequence_now_reaches_two_valid_sources():
    replies = [s("alphabad variant"), r(1),            # search A -> /rfc variant; read transport ok, no extractable fact
               s("nothing malformed site:x"),          # search B -> no_results
               s("beta corrected"),                    # corrected B: still ALLOWED
               s("alpha corrected"),                   # corrected A: still fits the 4-attempt budget
               r(2), r(3), FINISH]
    result, provider, session, port = run(replies)
    assert result.searches == 4 and "search_attempt_budget_exhausted" not in codes(result, "SEARCH")
    assert [f.source_url for f in result.deep_read_facts] == [D + "info/beta/", D + "info/alpha/"]      # valid_sources 1 then 2
    assert len(provider.prompts[-1].split("deep read of source")) == 3                                  # both blocks in SOURCE_DATA


# 4 / 5 ---------------------------------------------------------------------------------------------------------------
def test_4_5_first_then_second_valid_source():
    result, provider, _s, _port = run([s("alpha"), s("beta"), r(1), r(2), FINISH])
    assert [f.source_url for f in result.deep_read_facts] == [D + "info/alpha/", D + "info/beta/"]
    assert "deep read of source 1" in provider.prompts[-2] and "deep read of source 2" not in provider.prompts[-2]   # valid_sources == 1
    assert "deep read of source 2" in provider.prompts[-1]                                                           # valid_sources == 2


# 6 -------------------------------------------------------------------------------------------------------------------
def test_6_after_two_valid_sources_research_is_complete():
    result, _p, _s, port = run([s("alpha"), s("beta"), r(1), r(2), s("bad1"), r(1), FINISH])
    assert codes(result, "SEARCH") == ["research_sources_complete"] and codes(result, "DEEP_READ") == ["research_sources_complete"]
    assert result.searches == 2 and result.deep_reads == 2 and len(port.reads) == 2
    # store-level: never more than 2 admitted facts
    items = [("alpha", "x0"), ("beta", "x1"), ("bad1", "x2")]
    st = SourceFactStore(2, 4, 8000)
    for k, sid in items:
        st.admit(SourceCandidate(sid, {"title": ITEMS[k][0], "url": ITEMS[k][1], "description": "d"}, "d" * 64, "DATA_ONLY", "TOOL_RESULT"))
    for n, (k, sid) in enumerate(items[:2]):
        st.admit_deep_read(DeepReadCandidate(f"r{n}", sid, "d" * 64, ITEMS[k][1], ITEMS[k][1], GOODTEXT.format(n=n), False, None, f"{n}" * 64,
                                             "DATA_ONLY", "COMPUTER_OBSERVATION"), lambda c: True, validator)
    with pytest.raises(SourceFactRejected) as err:
        st.admit_deep_read(DeepReadCandidate("r2", "x2", "d" * 64, ITEMS["bad1"][1], ITEMS["bad1"][1], GOODTEXT.format(n=9), False, None, "9" * 64,
                                             "DATA_ONLY", "COMPUTER_OBSERVATION"), lambda c: True, validator)
    assert err.value.code == "deep_read_limit" and len(st.deep_facts()) == 2


# 7 / 8 ---------------------------------------------------------------------------------------------------------------
def test_7_8_four_search_attempts_tolerated_fifth_refused():
    result, _p, _s, _port = run([s("nothing 1"), s("nothing 2"), s("nothing 3"), s("nothing 4"), s("nothing 5"), FINISH])
    assert codes(result, "SEARCH") == ["no_results"] * 4 + ["search_attempt_budget_exhausted"] and result.searches == 4


# 9 / 10 / 12 ---------------------------------------------------------------------------------------------------------
def test_9_10_12_four_read_attempts_tolerated_fifth_refused_and_no_eviction():
    replies = [s("bad1"), s("bad2"), s("bad3"), s("bad4"), r(1), r(2), r(3), r(4), r(1), FINISH]
    result, provider, _s, port = run(replies)
    assert result.searches == 4 and result.deep_reads == 4 and len(port.reads) == 4        # ordinals 1..4 all still resolvable: no eviction
    assert codes(result, "DEEP_READ") == ["deep_fact_extraction_failed"] * 4 + ["deep_read_attempt_budget_exhausted"]
    assert result.deep_read_facts == []
    big = {"title": "t" * MAX_TITLE_CHARS, "url": "https://docs.example.org/" + "u" * (MAX_URL_CHARS - 30), "description": "x" * MAX_DESCRIPTION_CHARS}
    st = SourceFactStore(BUDGET.target_valid_sources, BUDGET.max_search_facts, BUDGET.max_source_context_chars)
    for i in range(4):
        st.admit(SourceCandidate(f"x{i}", {**big, "url": big["url"] + str(i)}, "d" * 64, "DATA_ONLY", "TOOL_RESULT"))
    assert [f.source_id for f in st.facts()] == ["x0", "x1", "x2", "x3"]
    old = SourceFactStore()
    for i in range(4):
        old.admit(SourceCandidate(f"x{i}", {**big, "url": big["url"] + str(i)}, "d" * 64, "DATA_ONLY", "TOOL_RESULT"))
    assert len(old.facts()) < 4                                                                 # the V1.4 default still evicts


# 11 ------------------------------------------------------------------------------------------------------------------
def test_11_same_canonical_url_already_admitted():
    result, _p, _s, port = run([s("alpha"), s("alpha again"), r(1), r(2), FINISH])
    assert port.reads == [D + "info/alpha/"] and result.deep_reads == 1
    assert codes(result, "DEEP_READ") == []
    assert any("DEEP_READ CONTROLLED_RETURN source_already_read" in line for line in result.log)


def test_11_repeated_source_already_read_never_exhausts_action_failure_budget():
    result, _p, _s, port = run([s("alpha"), r(1), r(1), r(1), r(1), FINISH])
    assert result.status == "VERIFIED"
    assert result.deep_reads == 1 and port.reads == [D + "info/alpha/"]
    assert all("DEEP_READ FAILED source_already_read" not in line for line in result.log)


# 13 ------------------------------------------------------------------------------------------------------------------
def retired_test_13_v14_default_budgets_codes_and_prompt_are_untouched():
    h = lambda x: hashlib.sha256(x.encode()).hexdigest()
    goal = AgentGoal("g", "goal text", max_steps=5)
    o = ComputerPageObservation("about:blank", "", "s", 0, (), "")
    assert (h(prompt_header()), h(prompt_header(True)), h(prompt_header(True, True)),
            h(build_prompt(goal, o, 1, None, ("https://a.example",), None, "blk", True, True))) == FROZEN_PROMPTS
    port = Port15(TEXTS)
    loop, _s = build_agent_loop(Script([s("alpha"), s("beta"), r(1), r(1), FINISH]), port, lambda ob: VerificationResult(True, {}),
                                allowed_origins=["https://wombat.example"], search=SearchConfig(("docs.example.org",), max_searches=1),
                                search_executor=brave(), allow_read=True, sleep=lambda x: None, max_consecutive_failures=20)
    result = loop.run(AgentGoal("g", GOAL_TEXT, max_steps=12))
    assert loop._research is None and loop._max_deep_reads == 1
    assert codes(result, "SEARCH") == ["search_budget_exhausted"] and codes(result, "DEEP_READ") == ["deep_read_budget_exhausted"]
    assert MAX_SEARCHES_PER_RUN == 5 and len(result.deep_read_facts) == 1                       # the V1.4 store admits a fact without any validator


# 14 ------------------------------------------------------------------------------------------------------------------
def test_14_prompt_states_domain_restriction_is_separate_and_the_budgets():
    p = prompt_header(True, True, research=BUDGET)
    assert "do not put site: operators or domain filters in the query" in p and "applies the authorized domain restriction separately" in p
    assert "at most 4 search attempts" in p and "at most 4 read attempts" in p and "once 2 sources have been read successfully" in p
    assert "At most one deep read" not in p
    for old in (prompt_header(True, True), prompt_header(True, True, 2), prompt_header(True)):
        assert "site:" not in old
    assert "site:" not in build_prompt(AgentGoal("g", "x", max_steps=3), ComputerPageObservation("about:blank", "", "s", 0, (), ""), 1, None,
                                       (), None, "", True, True, 1)


def test_15_budget_validation():
    for bad in ((0, 4, 4), (2, 1, 4), (2, 4, 1), (4, 4, 4), (2, 9, 4)):
        with pytest.raises(ValueError):
            ResearchBudget(*bad)
