"""V1_5_EXACT_VALUE_PRESERVATION micro-gate: ``browser.type_source_value`` (opt-in). The model picks WHICH governed source value goes into
WHICH control; the trusted runtime supplies the characters. Real governance chain, mock HTTP, scripted port. No live run."""
import json

import httpx
import pytest

from application.agent_loop import VerificationResult, build_prompt, prompt_header, PROMPT_HEADER
from application.agent_runtime import build_agent_loop
from application.source_facts import ResearchBudget, SearchConfig
from cognitive.agent_contracts import AgentGoal, DecisionParseError, parse_decision
from cognitive.source_value_contract import TypeSourceValueDecision, parse_decision_with_source_value
from governance.aep_executors import BraveSearchExecutor

from test_v1_4_deep_read_micro_gate import GOAL_TEXT, Script
from test_v1_5_multi_source_micro_gate import Port15

BUDGET = ResearchBudget(2, 4, 4)
CONFIG = SearchConfig(("docs.example.org",), result_limit=1, max_searches=4)
D = "https://docs.example.org/"
ITEMS = {"alpha": ("RFC 1: Alpha -- Handbook (v1.1) | Docs Site", D + "info/alpha_v2.1/"),
         "beta": ("RFC 22: Beta Manual | Docs Site", D + "info/beta/"),
         "bad": ("Bad One | Docs Site", D + "rfc/bad.html")}
TEXTS = {D + "info/alpha_v2.1/": "Header\nFACT: the identifier is 2817, see #5\nEnd.", D + "info/beta/": "Header\nFACT: the identifier is 1122\nEnd.",
         D + "rfc/bad.html": "Header\nno usable sentence\nEnd."}
FACT_A, FACT_B = "FACT: the identifier is 2817, see #5", "FACT: the identifier is 1122"
FINISH = '{"kind":"finish","claim":"done"}'


def extractor(text):
    return next((l for l in text.splitlines() if l.startswith("FACT:")), None)


def validator(fact):
    return extractor(fact.visible_text_excerpt) is not None


def brave():
    def handler(request):
        q = request.url.params["q"]
        key = next(k for k in ITEMS if k in q)
        title, url = ITEMS[key]
        return httpx.Response(200, json={"web": {"results": [{"title": title, "url": url, "description": "d " + key}]}})
    return BraveSearchExecutor(transport=httpx.MockTransport(handler), api_key_getter=lambda: "k")


def s(q):
    return json.dumps({"kind": "search", "query": q})


def r(n):
    return json.dumps({"kind": "read_source", "source": n})


def tsv(ref, source, field, **extra):
    args = {"ref": ref, "source": source, "field": field}
    args.update(extra)
    return json.dumps({"kind": "act", "action": "browser.type_source_value", "args": args})


def run(replies, research=BUDGET, source_value=True, extract=extractor, max_steps=30, **opts):
    port = Port15(TEXTS)
    provider = Script(replies)
    opts.setdefault("max_consecutive_failures", 20)
    if source_value:
        opts["enable_source_value"] = True
        opts["deep_fact_extractor"] = extract
    loop, session = build_agent_loop(provider, port, lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                     search=CONFIG, search_executor=brave(), allow_read=True, sleep=lambda x: None,
                                     research=research, deep_fact_validator=validator, **opts)
    result = loop.run(AgentGoal("g", GOAL_TEXT, max_steps=max_steps))
    return result, provider, session, port


READY = [s("alpha"), s("beta"), r(1), r(2)]       # two admitted sources, then the typing turns


def failed(result):
    return [l.split("TYPE_SOURCE_VALUE FAILED ")[1] for l in result.log if "TYPE_SOURCE_VALUE FAILED " in l]


# 1 -- punctuation / suffix preserved ---------------------------------------------------------------------------------
def test_1_title_with_punctuation_and_suffix_is_typed_verbatim():
    result, _p, _s, port = run(READY + [tsv("e1", 1, "title"), FINISH])
    assert port.typed == [ITEMS["alpha"][0]] and port.typed[0].endswith("| Docs Site") and " -- " in port.typed[0]
    assert failed(result) == []


# 2 -------------------------------------------------------------------------------------------------------------------
def test_2_url_is_typed_verbatim():
    _r, _p, _s, port = run(READY + [tsv("e1", 1, "url"), FINISH])
    assert port.typed == [ITEMS["alpha"][1]]


# 3 -------------------------------------------------------------------------------------------------------------------
def test_3_deep_fact_is_typed_verbatim():
    _r, _p, _s, port = run(READY + [tsv("e1", 1, "deep_fact"), tsv("e1", 2, "deep_fact"), FINISH])
    assert port.typed == [FACT_A, FACT_B]


# 4 -- no model-supplied literal ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("extra", [{"text": "made up"}, {"value": "made up"}, {"literal": "x"}])
def test_4_parser_rejects_any_literal_value(extra):
    with pytest.raises(DecisionParseError, match="args_fields"):
        parse_decision_with_source_value(tsv("e1", 1, "title", **extra), {"e1"}, allow_source_value=True)
    result, _p, _s, port = run(READY + [tsv("e1", 1, "title", **extra), FINISH])
    assert port.typed == [] and any("decision REJECTED args_fields" in l for l in result.log)


# 5 -- nonexistent source -----------------------------------------------------------------------------------------------
def test_5_nonexistent_source_fails_closed():
    result, _p, _s, port = run(READY + [tsv("e1", 3, "title"), FINISH])
    assert port.typed == [] and failed(result) == ["source_out_of_range"]
    early, _p2, _s2, port2 = run([tsv("e1", 1, "title"), FINISH])          # before any search exists
    assert port2.typed == [] and failed(early) == ["source_out_of_range"]


# 6 -- unsupported field ------------------------------------------------------------------------------------------------
def test_6_unsupported_field_fails_closed():
    for bad in ("description", "source_id", "title.x", "../title", ""):
        with pytest.raises(DecisionParseError, match="field_invalid"):
            parse_decision_with_source_value(tsv("e1", 1, bad), {"e1"}, allow_source_value=True)
    result, _p, _s, port = run(READY + [tsv("e1", 1, "deep_fact"), FINISH], extract=None)      # no trusted extractor configured
    assert port.typed == [] and failed(result) == ["field_unsupported"]


# 7 -- stale / unknown target ref ---------------------------------------------------------------------------------------
def test_7_stale_ref_is_rejected():
    with pytest.raises(DecisionParseError, match="unknown_ref"):
        parse_decision_with_source_value(tsv("e99", 1, "title"), {"e1", "e2"}, allow_source_value=True)
    result, _p, _s, port = run(READY + [tsv("e99", 1, "title"), FINISH])
    assert port.typed == [] and any("decision REJECTED unknown_ref" in l for l in result.log)


# 8 -- non-editable target ----------------------------------------------------------------------------------------------
def test_8_non_editable_target_is_rejected():
    result, _p, _s, port = run(READY + [tsv("e2", 1, "title"), FINISH])          # e2 is a button
    assert port.typed == [] and failed(result) == ["target_not_editable"]


# 9 -- ordinary browser.type unchanged ------------------------------------------------------------------------------------
def test_9_ordinary_browser_type_is_unchanged():
    plain = json.dumps({"kind": "act", "action": "browser.type", "args": {"ref": "e1", "text": "free text"}})
    _r, _p, _s, port = run(READY + [plain, FINISH])
    assert port.typed == ["free text"]
    assert parse_decision_with_source_value(plain, {"e1"}, allow_source_value=True) == parse_decision(plain, {"e1"})
    with pytest.raises(DecisionParseError, match="unknown_action"):
        parse_decision(tsv("e1", 1, "title"), {"e1"})


# 10 -- without the opt-in nothing changes ------------------------------------------------------------------------------------
def retired_test_10_without_opt_in_prompt_parser_and_behaviour_are_unchanged():
    assert prompt_header() == PROMPT_HEADER and prompt_header(True, True, 2, BUDGET) == prompt_header(True, True, 2, BUDGET, False)
    on, off = prompt_header(True, True, 2, BUDGET, True), prompt_header(True, True, 2, BUDGET, False)
    assert "type_source_value" in on and "type_source_value" not in off
    assert prompt_header(False, False, 1, None, True) == PROMPT_HEADER              # needs search; otherwise inert
    for banned in ("RFC", "Wombat", "| Docs", "Editor"):
        assert banned not in on.replace(off, "")                                    # the capability text names no benchmark
    # opt-out run: the action is an unknown action exactly as before; the loop never consults the store for it
    result, _p, _s, port = run(READY + [tsv("e1", 1, "title"), FINISH], source_value=False)
    assert port.typed == [] and any("decision REJECTED unknown_action" in l for l in result.log)
    with pytest.raises(DecisionParseError, match="unknown_action"):
        parse_decision_with_source_value(tsv("e1", 1, "title"), {"e1"})
    assert parse_decision_with_source_value(FINISH).claim == "done"


# 11 -- A/title never resolves to B/title -------------------------------------------------------------------------------------
def test_11_source_ordinals_do_not_cross():
    _r, _p, _s, port = run(READY + [tsv("e1", 2, "title"), tsv("e1", 1, "title"), tsv("e1", 2, "url"), FINISH])
    assert port.typed == [ITEMS["beta"][0], ITEMS["alpha"][0], ITEMS["beta"][1]]


# 12 -- value only from a correlated, ADMITTED source -------------------------------------------------------------------------
def test_12_value_only_from_an_admitted_source():
    result, _p, _s, port = run([s("alpha"), tsv("e1", 1, "title"), tsv("e1", 1, "deep_fact"), r(1), tsv("e1", 1, "title"), FINISH])
    assert failed(result) == ["source_not_admitted", "source_not_admitted"]            # a search attempt is not a valid source yet
    assert port.typed == [ITEMS["alpha"][0]]                                            # after the deep read was admitted
    bad, _p2, _s2, port2 = run([s("bad"), r(1), tsv("e1", 1, "title"), tsv("e1", 1, "deep_fact"), FINISH])
    assert bad.deep_read_facts == [] and port2.typed == [] and failed(bad) == ["source_not_admitted", "source_not_admitted"]


# 13 -- special characters survive --------------------------------------------------------------------------------------------
def test_13_special_characters_survive_verbatim():
    _r, _p, _s, port = run(READY + [tsv("e1", 1, "title"), tsv("e1", 1, "url"), tsv("e1", 1, "deep_fact"), FINISH])
    title, url, fact = port.typed
    assert " | " in title and " -- " in title and "(v1.1)" in title and ":" in title
    assert url.endswith("alpha_v2.1/") and fact.endswith(", see #5")


# 14 -- evidence: identity and field, not the value ---------------------------------------------------------------------------------
def test_14_evidence_records_identity_and_field_without_the_value():
    result, _p, session, port = run(READY + [tsv("e1", 1, "title"), FINISH])
    (use,) = result.source_value_uses
    assert (use.source, use.field, use.ref) == (1, "title", "e1") and use.source_id == result.source_facts[0].source_id
    assert use.value_length == len(ITEMS["alpha"][0]) and len(use.value_digest) == 64
    assert ITEMS["alpha"][0] not in repr(use)
    decided = [l for l in result.log if "Agnes decided browser.type " in l]
    assert len(decided) == 1 and "source_value source=1 field=title" in decided[0] and ITEMS["alpha"][0] not in decided[0]
    assert port.typed == [ITEMS["alpha"][0]]                                            # the governed browser.type still received the value
