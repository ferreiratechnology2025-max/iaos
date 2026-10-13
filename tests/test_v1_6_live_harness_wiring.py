from scripts.agent_wombat_v16_e2e import (
    ALLOWED_ORIGINS, GOAL, MAX_DEEP_READS, MAX_STEPS, RESEARCH, SEARCH,
    PUBLISHER_POLICY, build_v16_loop, synthetic_wiring_report,
)
from application.research_completion import registered_domain_resolver
from application.research_state import render_research_state
from application.source_facts import DeepReadFact, ResearchBudget, SourceFact


def test_dedicated_entrypoint_freezes_v16_configuration():
    assert MAX_DEEP_READS == 2 and MAX_STEPS == 80
    assert RESEARCH.target_valid_sources == 2
    assert RESEARCH.max_search_attempts == 4 and RESEARCH.max_deep_read_attempts == 4
    assert SEARCH.allowed_domains == ("esa.int", "nasa.gov")
    assert ALLOWED_ORIGINS == ("https://websitewombat.app",)


def test_goal_does_not_leak_facts_or_canonical_urls():
    prompt = GOAL.objective
    for value in ("Nov. 16, 2022", "2 March 2004",
                  "nasa.gov/mission/artemis-i", "esa.int/Science_Exploration/Space_Science/Rosetta"):
        assert value not in prompt


def test_real_entrypoint_wiring_and_mutants():
    report = synthetic_wiring_report()
    assert report["live"] is False
    assert report["baseline"].verified
    assert all(not item.verified for item in report["mutants"].values())


def test_entrypoint_composes_frozen_loop_options_without_running_it():
    loop, session = build_v16_loop(object(), object(), lambda _observation: None)
    assert loop._research == RESEARCH
    assert loop._source_value is True
    assert loop._deep_extractor is not None
    assert loop._max_searches == 4 and loop._max_deep_reads == 4
    assert loop._max_admitted_sources == 4 and loop._research_completion_policy is PUBLISHER_POLICY
    assert loop._require_research_complete_before_artifact is True
    assert session._allow_read is True


def test_core_budget_does_not_claim_publisher_diversity():
    # V1.6 must not silently reinterpret the frozen V1.5 valid-source count.
    assert not hasattr(RESEARCH, "target_publishers")
    assert RESEARCH.target_valid_sources == 2


def _evidence(url_a="https://www.nasa.gov/mission/artemis-i/", url_b="https://www.nasa.gov/mission/other/"):
    facts = [SourceFact("web.search", "search-A", "A", url_a, "", "a" * 64),
             SourceFact("web.search", "search-B", "B", url_b, "", "b" * 64)]
    deep = [DeepReadFact("search_bound_deep_read", "read-A", "search-A", url_a, "Artemis I Nov. 16, 2022", False, "c" * 64),
            DeepReadFact("search_bound_deep_read", "read-B", "search-B", url_b, "Other", False, "d" * 64)]
    return facts, deep


def test_v16_policy_keeps_research_open_for_two_nasa_sources_and_projection_exposes_gap():
    facts, deep = _evidence()
    state = PUBLISHER_POLICY.evaluate(RESEARCH, facts, deep)
    assert state.valid_sources == 2 and state.represented_publishers == frozenset({"NASA"})
    assert state.complete is False
    projected = render_research_state(RESEARCH, facts, deep, {}, 2, 2,
                                      lambda url: url.rstrip("/"), PUBLISHER_POLICY)
    assert "required publishers: 1 / 2" in projected
    assert "NASA: SATISFIED" in projected and "ESA: MISSING" in projected
    assert "research_sources_complete: YES" not in projected


def test_registered_domain_resolution_is_not_substring_matching():
    resolve = registered_domain_resolver({"nasa.gov": "NASA", "esa.int": "ESA"})
    assert resolve("https://www.nasa.gov/x") == "NASA"
    assert resolve("https://science.nasa.gov/x") == "NASA"
    assert resolve("https://www.esa.int/x") == "ESA"
    assert resolve("https://notnasa.gov/x") is None
    assert resolve("https://nasa.gov.example.com/x") is None


def test_redundant_nasa_can_be_retained_until_esa_arrives():
    facts, deep = _evidence()
    facts.append(SourceFact("web.search", "search-C", "C", "https://www.esa.int/Science_Exploration/Space_Science/Rosetta", "", "e" * 64))
    deep.append(DeepReadFact("search_bound_deep_read", "read-C", "search-C",
                             "https://www.esa.int/Science_Exploration/Space_Science/Rosetta",
                             "Rosetta 2 March 2004", False, "f" * 64))
    state = PUBLISHER_POLICY.evaluate(RESEARCH, facts, deep)
    assert state.valid_sources == 3
    assert state.represented_publishers == frozenset({"NASA", "ESA"})
    assert state.complete is True
