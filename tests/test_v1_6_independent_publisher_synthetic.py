from dataclasses import replace

from application.v16_independent_publisher import (
    ESA, NASA, REQUIRED_DOCUMENTS, PublisherChain, make_synthetic_run, synthetic_observation, verify_v16,
)
from application.research_completion import RequiredPublisherCompletionPolicy, registered_domain_resolver
from application.source_facts import DeepReadFact, ResearchBudget, SourceFact


def result(run=None):
    run = run or make_synthetic_run()
    return verify_v16(run, synthetic_observation(run))


def test_v16_baseline_fixture_and_dual_chains_pass():
    r = result()
    assert r.verified
    assert r.facts["CHAIN_A_VALID"] and r.facts["CHAIN_B_VALID"]
    assert r.facts["PUBLISHERS_DISTINCT"]
    assert r.facts["ARTIFACT_CONTAINS_FACT_A"] and r.facts["ARTIFACT_CONTAINS_FACT_B"]


def test_structured_admission_remains_authoritative_when_excerpt_is_truncated():
    run = make_synthetic_run(chain_a=PublisherChain(NASA, "search-A", "read-A", NASA.canonical_document,
                                                     NASA.canonical_document, "Artemis I", NASA.fact))
    assert result(run).verified


def test_empty_excerpt_does_not_invalidate_structured_admission():
    base = make_synthetic_run()
    run = replace(base, chain_a=replace(base.chain_a, read_text=""),
                  chain_b=replace(base.chain_b, read_text=""))
    assert run.chain_a.admitted_fact and run.chain_b.admitted_fact
    assert all((run.chain_a.admitted_fact, run.chain_b.admitted_fact))
    from application.v16_independent_publisher import research_assurance
    assert research_assurance(run)


def test_excerpt_only_fact_without_structured_admission_fails_closed():
    base = make_synthetic_run()
    run = replace(base, chain_a=replace(base.chain_a, admitted_fact=None))
    from application.v16_independent_publisher import research_assurance
    assert not research_assurance(run)


def test_invalid_source_binding_fails_closed_even_with_admitted_fact():
    base = make_synthetic_run()
    run = replace(base, chain_a=replace(base.chain_a, read_url=ESA.canonical_document))
    from application.v16_independent_publisher import research_assurance
    assert not research_assurance(run)


def test_completeness_is_independent_of_excerpt_length_and_order():
    base = make_synthetic_run()
    short = replace(base, chain_a=replace(base.chain_a, read_text="x"),
                    chain_b=replace(base.chain_b, read_text="y"))
    reordered = replace(base, chain_a=replace(base.chain_a, read_text=""),
                        chain_b=replace(base.chain_b, read_text="prefix"))
    from application.v16_independent_publisher import research_assurance
    assert research_assurance(short)
    assert research_assurance(reordered)


def test_artifact_mutants_fail():
    base = make_synthetic_run()
    for text in ("Nov. 16, 2022", "Artemis I", "2 March 2004", "Rosetta"):
        assert not result(replace(base, artifact_preview=base.artifact_preview.replace(text, ""))).verified


def test_chain_and_provenance_mutants_fail():
    base = make_synthetic_run()
    assert not result(replace(base, chain_b=replace(base.chain_b, read_execution_id="read-A"))).verified
    assert not result(replace(base, chain_b=replace(base.chain_b, search_execution_id="search-A"))).verified
    assert not result(replace(base, chain_b=replace(base.chain_b, read_url=NASA.canonical_document))).verified
    assert not result(replace(base, chain_a=replace(base.chain_a, read_url=ESA.canonical_document))).verified
    assert not result(replace(base, chain_a=replace(base.chain_a, admitted_fact=ESA.fact))).verified
    assert not result(replace(base, chain_b=replace(base.chain_b, admitted_fact=NASA.fact))).verified


def test_identity_domain_and_missing_chain_mutants_fail():
    base = make_synthetic_run()
    same_publisher = replace(base.chain_b, publisher=NASA)
    assert not result(replace(base, chain_b=same_publisher)).verified
    same_domain = replace(base.chain_b, publisher=replace(ESA, registered_domain=NASA.registered_domain))
    assert not result(replace(base, chain_b=same_domain)).verified
    assert not result(replace(base, chain_b=replace(base.chain_b, admitted_fact=None))).verified
    assert not result(replace(base, chain_a=replace(base.chain_a, admitted_fact=None))).verified


def test_formal_counterfactuals_and_fresh_observation():
    base = make_synthetic_run()
    assert result(base).verified
    assert not result(replace(base, artifact_preview=base.artifact_preview.replace(NASA.fact, ""))).verified
    assert not result(replace(base, artifact_preview=base.artifact_preview.replace(ESA.fact, ""))).verified
    stale = synthetic_observation(base)
    stale = replace(stale, revision=0)
    assert not verify_v16(base, stale).verified


def test_required_document_identity_separates_publisher_from_document_completion():
    policy = RequiredPublisherCompletionPolicy(
        frozenset(("NASA", "ESA")),
        registered_domain_resolver({NASA.registered_domain: "NASA", ESA.registered_domain: "ESA"}),
        required_documents=REQUIRED_DOCUMENTS,
    )
    budget = ResearchBudget()
    facts = [
        SourceFact("web.search", "n", "NASA", NASA.canonical_document, "", "n" * 64),
        SourceFact("web.search", "e", "ESA", "https://www.esa.int/Science_Exploration/Space_Science/Rosetta/Rosetta_factsheet", "", "e" * 64),
    ]
    deep = [
        DeepReadFact("search_bound_deep_read", "rn", "n", NASA.canonical_document,
                     "Artemis I Nov. 16, 2022", False, "a" * 64),
        DeepReadFact("search_bound_deep_read", "re", "e", facts[1].url,
                     "Rosetta 2 March 2004", False, "b" * 64),
    ]
    state = policy.evaluate(budget, facts, deep)
    assert state.valid_sources == 2
    assert state.represented_publishers == frozenset({"NASA", "ESA"})
    assert state.represented_documents == frozenset({NASA.canonical_document})
    assert state.required_documents == frozenset(document.canonical_url for document in REQUIRED_DOCUMENTS)
    assert state.complete is False


def test_required_document_identity_positive_and_wrong_document_mutants():
    policy = RequiredPublisherCompletionPolicy(
        frozenset(("NASA", "ESA")),
        registered_domain_resolver({NASA.registered_domain: "NASA", ESA.registered_domain: "ESA"}),
        required_documents=REQUIRED_DOCUMENTS,
    )
    facts = [SourceFact("web.search", "n", "NASA", NASA.canonical_document, "", "n" * 64),
             SourceFact("web.search", "e", "ESA", ESA.canonical_document, "", "e" * 64)]
    deep = [DeepReadFact("search_bound_deep_read", "rn", "n", NASA.canonical_document,
                         "Artemis I Nov. 16, 2022", False, "a" * 64),
            DeepReadFact("search_bound_deep_read", "re", "e", ESA.canonical_document,
                         "Rosetta 2 March 2004", False, "b" * 64)]
    assert policy.evaluate(ResearchBudget(), facts, deep).complete


def test_chain_diagnostics_explain_required_document_mismatch():
    facts = make_synthetic_run(chain_b=PublisherChain(
        ESA, "search-B", "read-B", ESA.canonical_document,
        "https://www.esa.int/Science_Exploration/Space_Science/Rosetta/Rosetta_factsheet",
        "ESA mission page: Rosetta. Launch: 2 March 2004.", ESA.fact,
    ))
    result = verify_v16(facts, synthetic_observation(facts))
    assert result.verified is False
    assert result.diagnostics["chain_a"]["required_document_match"] is True
    assert result.diagnostics["chain_b"]["required_document_match"] is False
    assert result.diagnostics["chain_b"]["chain_validation_failure_reason"] == "required_canonical_url_mismatch"
