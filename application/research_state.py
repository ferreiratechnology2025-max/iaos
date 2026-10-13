"""V1.5 research-state PROJECTION (pure, derived): what the runtime already knows about the research, told to cognition.

It is NOT a new source of truth and keeps no state of its own. Inputs are the objects that already exist:
ResearchBudget, the SourceFactStore (discovered sources, admitted deep reads), the terminal-rejection set (keyed by canonical
URL) and the executed-attempt counters. Statuses are a closed vocabulary:

  DISCOVERED           listed by a search, not read to an admitted fact, not terminally rejected
  ADMITTED             its document has an admitted DeepReadFact
  TERMINALLY_REJECTED  a sound read whose content failed the trusted extraction (the proven terminal-rejection mechanism)

Transient failures (timeout, restore failure, challenge, ...) never create a status: such a source stays DISCOVERED.
The projection names no query, no site and adds no page content; it does not authorize or refuse anything (enforcement stays in the runtime).
"""
from typing import Callable, Iterable, Mapping, Optional, Sequence

from application.source_facts import DeepReadFact, ResearchBudget, SourceFact
from application.research_completion import CountBasedResearchCompletionPolicy, ResearchCompletionPolicy

DISCOVERED, ADMITTED, TERMINALLY_REJECTED = "DISCOVERED", "ADMITTED", "TERMINALLY_REJECTED"


def source_statuses(facts: Sequence[SourceFact], deep_facts: Sequence[DeepReadFact], rejected: Mapping[str, object],
                    canonical: Callable[[str], Optional[str]]) -> list:
    admitted_docs = {d.source_url for d in deep_facts}
    result = []
    for fact in facts:
        url = canonical(fact.url)
        if url is not None and url in admitted_docs:
            result.append(ADMITTED)
        elif url is not None and url in rejected:
            result.append(TERMINALLY_REJECTED)
        else:
            result.append(DISCOVERED)
    return result


def render_research_state(budget: ResearchBudget, facts: Sequence[SourceFact], deep_facts: Sequence[DeepReadFact],
                          rejected: Mapping[str, object], searches_used: int, deep_reads_used: int,
                          canonical: Callable[[str], Optional[str]],
                          completion_policy: Optional[ResearchCompletionPolicy] = None) -> str:
    statuses = source_statuses(facts, deep_facts, rejected, canonical)
    policy = completion_policy or CountBasedResearchCompletionPolicy()
    completion = policy.evaluate(budget, facts, deep_facts)
    valid = completion.valid_sources
    readable = [i for i, s in enumerate(statuses, 1) if s == DISCOVERED]
    lines = ["RESEARCH STATE (derived by the runtime from its own records):",
             f"- valid sources: {valid} / {budget.target_valid_sources}",
             f"- search attempts: {searches_used} / {budget.max_search_attempts} used",
             f"- deep-read attempts: {deep_reads_used} / {budget.max_deep_read_attempts} used"]
    if statuses:
        lines.append("- sources:")
        lines += [f"  - source {i} [{s}]" for i, s in enumerate(statuses, 1)]
    else:
        lines.append("- sources: none yet")
    lines.append("- readable discovered sources: " + (", ".join(f"source {i}" for i in readable) if readable else "none"))
    lines.append("- further search is available: " + ("YES" if not completion.complete and searches_used < budget.max_search_attempts else "NO"))
    if completion.required_publishers:
        lines.append(f"- required publishers: {len(completion.represented_publishers)} / {len(completion.required_publishers)}")
        for publisher in sorted(completion.required_publishers):
            lines.append(f"  - {publisher}: {'SATISFIED' if publisher in completion.represented_publishers else 'MISSING'}")
    if completion.required_documents:
        lines.append(f"- required documents: {len(completion.represented_documents)} / {len(completion.required_documents)}")
        for document in sorted(completion.required_documents):
            lines.append(f"  - {document}: {'SATISFIED' if document in completion.represented_documents else 'MISSING'}")
    if completion.complete:
        lines.append("- research_sources_complete: YES")
    return "\n".join(lines)
