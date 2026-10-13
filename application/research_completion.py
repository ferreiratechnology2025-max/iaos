"""Opt-in research completion policies.

The count-based policy is the V1.5/default behavior.  Publisher-aware
completion is a separate semantic policy and never changes ResearchBudget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional, Protocol, Sequence
from urllib.parse import urlparse

from application.source_facts import DeepReadFact, ResearchBudget, SourceFact
from governance.origin import host_matches_allowed_domain


@dataclass(frozen=True)
class RequiredDocumentSpec:
    """One exact document identity required by an opt-in completion policy."""

    publisher: str
    registered_domain: str
    canonical_url: str


@dataclass(frozen=True)
class ResearchCompletion:
    complete: bool
    valid_sources: int
    represented_publishers: FrozenSet[str] = frozenset()
    required_publishers: FrozenSet[str] = frozenset()
    represented_documents: FrozenSet[str] = frozenset()
    required_documents: FrozenSet[str] = frozenset()


class ResearchCompletionPolicy(Protocol):
    def evaluate(self, budget: ResearchBudget, facts: Sequence[SourceFact],
                 deep_facts: Sequence[DeepReadFact]) -> ResearchCompletion: ...

    def prompt_contract(self, budget: ResearchBudget) -> str: ...


@dataclass(frozen=True)
class CountBasedResearchCompletionPolicy:
    """Frozen V1.5/default semantics: completion is only the valid-source count."""

    def evaluate(self, budget: ResearchBudget, facts: Sequence[SourceFact],
                 deep_facts: Sequence[DeepReadFact]) -> ResearchCompletion:
        valid = len(deep_facts)
        return ResearchCompletion(valid >= budget.target_valid_sources, valid)

    def prompt_contract(self, budget: ResearchBudget) -> str:
        return (f"Research is complete once {budget.target_valid_sources} sources have been read successfully; "
                "after that no further search or read is accepted.")


PublisherResolver = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class RequiredPublisherCompletionPolicy:
    """Completion requiring a predeclared publisher set, derived from admitted URLs."""

    required_publishers: FrozenSet[str]
    publisher_resolver: PublisherResolver
    required_documents: tuple[RequiredDocumentSpec, ...] = ()

    def __post_init__(self) -> None:
        if self.required_documents:
            publishers = frozenset(document.publisher for document in self.required_documents)
            if publishers != self.required_publishers:
                raise ValueError("required_document_publishers_mismatch")

    def evaluate(self, budget: ResearchBudget, facts: Sequence[SourceFact],
                 deep_facts: Sequence[DeepReadFact]) -> ResearchCompletion:
        by_id = {fact.source_id: fact for fact in facts}
        represented = set()
        for deep in deep_facts:
            source = by_id.get(deep.search_execution_id)
            if source is not None:
                publisher = self.publisher_resolver(deep.source_url)
                if publisher is not None:
                    represented.add(publisher)
        represented_frozen = frozenset(represented)
        if self.required_documents:
            required_urls = frozenset(document.canonical_url for document in self.required_documents)
            represented_urls = frozenset(
                document.canonical_url
                for document in self.required_documents
                if any(deep.source_url == document.canonical_url for deep in deep_facts
                       if self.publisher_resolver(deep.source_url) == document.publisher)
            )
            complete = len(deep_facts) >= budget.target_valid_sources and required_urls <= represented_urls
        else:
            # Preserve the historical publisher-only policy for callers that did
            # not opt into exact required-document identity.
            required_urls = frozenset()
            represented_urls = frozenset()
            complete = len(deep_facts) >= budget.target_valid_sources and self.required_publishers <= represented_frozen
        return ResearchCompletion(complete, len(deep_facts), represented_frozen, self.required_publishers,
                                  represented_urls, required_urls)

    def prompt_contract(self, budget: ResearchBudget) -> str:
        required = ", ".join(sorted(self.required_publishers))
        if self.required_documents:
            documents = ", ".join(document.canonical_url for document in self.required_documents)
            return (f"Research is complete only when at least {budget.target_valid_sources} sources have been read successfully "
                    f"and each required publisher's exact canonical document is admitted ({documents}); "
                    "after that no further search or read is accepted.")
        return (f"Research is complete only when at least {budget.target_valid_sources} sources have been read successfully "
                f"and all required publishers are represented ({required}); after that no further search or read is accepted.")


def registered_domain_resolver(registrations: dict[str, str]) -> PublisherResolver:
    """Return a safe exact/subdomain resolver; substring matches are rejected."""
    normalized = {domain.lower().lstrip("."): publisher for domain, publisher in registrations.items()}

    def resolve(url: str) -> Optional[str]:
        try:
            host = (urlparse(url).hostname or "").lower().rstrip(".")
        except ValueError:
            return None
        for domain, publisher in normalized.items():
            if host_matches_allowed_domain(host, domain):
                return publisher
        return None

    return resolve
