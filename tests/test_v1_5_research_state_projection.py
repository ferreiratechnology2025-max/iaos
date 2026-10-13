"""V1_5_RESEARCH_STATE_PROJECTION_V1 micro-gate (synthetic). The RESEARCH STATE block is a pure projection of records the runtime
already holds (ResearchBudget, SourceFactStore, terminal-rejection set, attempt counters): it must show cognition what the runtime
knows, persistently. Replays live attempt 7, where source 1 was terminally rejected but still looked like an ordinary source."""
import json
import re

from application.agent_loop import VerificationResult
from application.agent_runtime import build_agent_loop
from application.research_state import render_research_state
from application.source_facts import ResearchBudget
from cognitive.agent_contracts import AgentGoal

from test_v1_4_deep_read_micro_gate import GOAL_TEXT
from test_v1_5_terminal_source_rejection import BUDGET, CONFIG, D, PortX, Script, X, Y, brave, r, run, s, validator

TYPE = json.dumps({"kind": "act", "action": "browser.type", "args": {"ref": "e1", "text": "x"}})
Z = D + "info/z/"


def block(prompt):
    lines = prompt.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("RESEARCH STATE"))
    out = [lines[start]]
    for l in lines[start + 1:]:
        if not l.startswith(("- ", "  - ")):
            break
        out.append(l)
    return "\n".join(out)


def statuses(prompt):
    return dict((int(m.group(1)), m.group(2)) for m in re.finditer(r"source (\d+) \[(\w+)\]", block(prompt)))


REPLAY = [s("combined"), r(1), TYPE, s("ypage"), r(2), s("zpage"), r(3), '{"kind":"finish","claim":"done"}']


def test_01_no_sources_shows_zero_of_target_and_correct_budgets():
    _res, provider, _port = run(REPLAY)
    b = block(provider.prompts[0])
    assert "valid sources: 0 / 2" in b and "search attempts: 0 / 4 used" in b and "deep-read attempts: 0 / 4 used" in b
    assert "sources: none yet" in b and "readable discovered sources: none" in b and "further search is available: YES" in b


def test_02_09_discovered_source_is_labelled_and_readable():
    _res, provider, _port = run(REPLAY)
    p = provider.prompts[1]
    assert statuses(p) == {1: "DISCOVERED"} and "readable discovered sources: source 1" in block(p)


def test_04_08_extraction_failure_marks_terminally_rejected_and_removes_it_from_readable_list():
    _res, provider, _port = run(REPLAY)
    p = provider.prompts[2]
    b = block(p)
    assert statuses(p) == {1: "TERMINALLY_REJECTED"} and "valid sources: 0 / 2" in b
    assert "readable discovered sources: none" in b and "further search is available: YES" in b
    assert "search attempts: 1 / 4 used" in b and "deep-read attempts: 1 / 4 used" in b
    assert "source 1 (web.search" in p      # the source stays visible in the history (audit), it is only labelled


def test_05_terminal_rejection_persists_after_an_unrelated_successful_browser_action():
    _res, provider, _port = run(REPLAY)
    assert statuses(provider.prompts[3]) == {1: "TERMINALLY_REJECTED"}      # prompt right after the successful browser.type
    assert "readable discovered sources: none" in block(provider.prompts[3])


def test_06_terminal_rejection_persists_after_invalid_json():
    _res, provider, _port = run([s("combined"), r(1), "junk", "junk", s("ypage"), r(2), '{"kind":"finish","claim":"done"}'])
    assert statuses(provider.prompts[3]) == {1: "TERMINALLY_REJECTED"} and statuses(provider.prompts[4]) == {1: "TERMINALLY_REJECTED"}


def test_07_same_canonical_url_rediscovered_under_another_search_id_is_still_rejected():
    res, provider, _port = run([s("combined"), r(1), s("xagain"), r(2), '{"kind":"finish","claim":"done"}'])
    assert res.source_facts[0].source_id != res.source_facts[1].source_id
    p = provider.prompts[3]
    assert statuses(p) == {1: "TERMINALLY_REJECTED", 2: "TERMINALLY_REJECTED"} and "readable discovered sources: none" in block(p)


def test_09_10_11_discovered_is_readable_admitted_is_not_and_valid_counts_admitted_only():
    _res, provider, _port = run(REPLAY)
    p4, p5 = provider.prompts[4], provider.prompts[5]
    assert statuses(p4) == {1: "TERMINALLY_REJECTED", 2: "DISCOVERED"} and "readable discovered sources: source 2" in block(p4)
    b5 = block(p5)
    assert statuses(p5) == {1: "TERMINALLY_REJECTED", 2: "ADMITTED"} and "valid sources: 1 / 2" in b5
    assert "readable discovered sources: none" in b5 and "further search is available: YES" in b5
    assert "research_sources_complete" not in b5


def test_12_13_counters_reflect_executed_attempts_and_a_blocked_reread_adds_none():
    res, provider, port = run([s("combined"), r(1), r(1), r(1), TYPE, '{"kind":"finish","claim":"done"}'])
    assert port.reads == [X] and res.deep_reads == 1
    b = block(provider.prompts[4])
    assert "deep-read attempts: 1 / 4 used" in b and "search attempts: 1 / 4 used" in b


def test_14_reaching_the_target_makes_completion_visible():
    _res, provider, _port = run(REPLAY)
    b = block(provider.prompts[7])
    assert "valid sources: 2 / 2" in b and "research_sources_complete: YES" in b and "further search is available: NO" in b
    assert statuses(provider.prompts[7]) == {1: "TERMINALLY_REJECTED", 2: "ADMITTED", 3: "ADMITTED"}


def test_15_transient_failure_never_creates_a_terminal_status():
    _res, provider, _port = run([s("ypage"), r(1), TYPE, '{"kind":"finish","claim":"done"}'], faults={Y: "raise"})
    p = provider.prompts[2]
    assert statuses(p) == {1: "DISCOVERED"} and "readable discovered sources: source 1" in block(p)
    assert "deep-read attempts: 1 / 4 used" in block(p)


def test_16_without_research_mode_there_is_no_projection_and_the_prompt_is_unchanged():
    port, provider = PortX(), Script([s("combined"), r(1), '{"kind":"finish","claim":"done"}'])
    loop, _s = build_agent_loop(provider, port, lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                search=CONFIG, search_executor=brave(), allow_read=True, sleep=lambda x: None,
                                deep_fact_validator=validator, max_consecutive_failures=20)
    loop.run(AgentGoal("g", GOAL_TEXT, max_steps=20))
    assert all("RESEARCH STATE" not in p for p in provider.prompts)


def test_17_18_no_site_specific_wording_and_no_page_content_in_the_projection():
    _res, provider, _port = run(REPLAY)
    for p in provider.prompts:
        b = block(p)
        for banned in ("RFC", "/info/", "Wombat", "rfc", "http", "FACT:", "no usable sentence", "identifier"):
            assert banned not in b, banned


def test_projection_is_pure_and_keeps_no_state():
    budget = ResearchBudget(2, 4, 4)
    a = render_research_state(budget, (), (), {}, 0, 0, lambda u: u)
    assert a == render_research_state(budget, (), (), {}, 0, 0, lambda u: u)
    assert "TERMINALLY_REJECTED" not in a and "valid sources: 0 / 2" in a
