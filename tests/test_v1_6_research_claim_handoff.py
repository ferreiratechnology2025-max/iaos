import pytest

from application.source_facts import (
    DeepReadCandidate,
    SourceCandidate,
    SourceFactRejected,
    SourceFactStore,
)
from cognitive.agent_contracts import ResearchClaimProposal, parse_decision


URL = "https://source.example/project"


def _store_with_read(read_id="R1", *, excerpt="prefix without the semantic fact"):
    store = SourceFactStore(max_deep_reads=2, max_search_facts=2, max_source_context_chars=4096,
                            max_admitted_sources=2)
    search = SourceCandidate("S1", {"title": "Project", "url": URL, "description": "source"},
                             "search-digest", "DATA_ONLY", "TOOL_RESULT")
    store.admit(search)
    read = DeepReadCandidate(read_id, "S1", "search-digest", URL, URL, excerpt, True, None,
                             "read-digest", "DATA_ONLY", "COMPUTER_OBSERVATION")
    deep = store.admit_deep_read(read, lambda _: True, fact_extractor=None)
    return store, deep


def test_llm_claim_is_bound_by_kernel_without_extracting_visible_text():
    store, deep = _store_with_read()
    admitted = store.admit_claim(ResearchClaimProposal(
        "Project launch date — 14 February 2020", deep.read_execution_id,
    ))

    assert admitted.claim_text == "Project launch date — 14 February 2020"
    assert admitted.source_url == URL
    assert admitted.search_execution_id == "S1"
    assert admitted.read_execution_id == "R1"
    assert admitted.evidence_digest == "read-digest"
    assert store.deep_facts()[0].admitted_fact == admitted


def test_unknown_read_fails_closed():
    store, _ = _store_with_read()
    with pytest.raises(SourceFactRejected, match="read_not_found"):
        store.admit_claim(ResearchClaimProposal("claim", "UNKNOWN"))


def test_missing_claim_fails_closed_without_creating_admitted_fact():
    store, deep = _store_with_read()
    assert deep.admitted_fact is None
    assert all(item.admitted_fact is None for item in store.deep_facts())


def test_two_claims_keep_distinct_read_bindings():
    store = SourceFactStore(max_deep_reads=2, max_search_facts=2, max_source_context_chars=4096,
                            max_admitted_sources=2)
    for suffix in ("A", "B"):
        url = f"{URL}-{suffix}"
        store.admit(SourceCandidate(f"S{suffix}", {"title": suffix, "url": url, "description": "source"},
                                    f"search-digest-{suffix}", "DATA_ONLY", "TOOL_RESULT"))
        store.admit_deep_read(
            DeepReadCandidate(f"R{suffix}", f"S{suffix}", f"search-digest-{suffix}", url, url,
                              "truncated text", True, None, f"read-digest-{suffix}",
                              "DATA_ONLY", "COMPUTER_OBSERVATION"),
            lambda _: True,
        )
    admitted = [store.admit_claim(ResearchClaimProposal(f"claim {s}", f"R{s}")) for s in ("A", "B")]
    assert [(item.read_execution_id, item.evidence_digest) for item in admitted] == [
        ("RA", "read-digest-A"), ("RB", "read-digest-B")
    ]


def test_claim_handoff_parser_accepts_only_claim_and_read_id():
    decision = parse_decision(
        '{"kind":"research_handoff","claims":[{"claim_text":"launch date — 14 February 2020",'
        '"read_execution_id":"R1"}]}',
        allow_research_handoff=True,
    )
    assert decision.claims[0].claim_text.startswith("launch date")
    assert decision.claims[0].read_execution_id == "R1"
