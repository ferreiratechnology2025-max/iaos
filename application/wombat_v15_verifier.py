"""V1.5 multi-document verifier: composition over the FROZEN V1.4 verifier (wombat_v14_verifier / V1.3 are not modified).

PASS requires exactly TWO independent governed chains, each one proven end to end by the V1.4 rules
(search execution -> SearchSourceBinding -> read execution -> DeepReadFact -> deep fact), and BOTH chains' title, URL and
deep fact inside the single artifact preview of the same final observation.

The chains must be different DOCUMENTS: distinct search executions, read executions, canonical URLs, titles and deep facts.
Two searches that return the same canonical URL are one document, not two. "Multi-document" is not "independent publishers".
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

from application.agent_loop import VerificationResult
from application.computer_runtime import ComputerPageObservation
from application.wombat_v14_verifier import _binds, deep_correlation, make_v14_verifier
from governance.bound_read import READ_SEARCH_SOURCE_ACTION

CHAIN_LABELS = ("A", "B")


def _norm(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _scoped(sid: str, items: Sequence[Any]) -> list:
    return [i for i in items if getattr(i, "search_execution_id", None) == sid]


def read_linked_to_chain(events: Sequence[Mapping[str, Any]], read_execution_id: str, search_execution_id: str, canonical_url: str) -> bool:
    """Cross-correlation A/B: the read EXECUTION_RESULT carrying this fact must point (authorization_id) at the AUTHORIZATION event
    whose resource is this chain's canonical URL and whose binding names this chain's search execution - not the other chain's."""
    for result in events:
        if result.get("kind") == "EXECUTION_RESULT" and result.get("action") == READ_SEARCH_SOURCE_ACTION \
                and result.get("execution_id") == read_execution_id and result.get("ok") is True:
            for auth in events:
                if auth.get("kind") == "AUTHORIZATION" and auth.get("action") == READ_SEARCH_SOURCE_ACTION \
                        and auth.get("event_id") == result.get("authorization_id") and auth.get("resource") == canonical_url \
                        and _binds(auth, search_execution_id):
                    return True
    return False


def chain_expectations(ledger: Sequence[Mapping[str, Any]], events: Iterable[Mapping[str, Any]], search_effects: Sequence[Any],
                       bindings: Sequence[Any], deep_facts: Sequence[Any], read_effects: Sequence[Any]):
    """([(label, ledger_entry, expected-or-None, reason)], distinctness_failures)."""
    events = list(events)
    chains = []
    for label, entry in zip(CHAIN_LABELS, ledger):
        sid = entry.get("execution_id")
        expected, reason = deep_correlation([entry], events, search_effects, _scoped(sid, bindings), _scoped(sid, deep_facts), read_effects)
        if expected is not None and not read_linked_to_chain(events, expected["read_execution_id"], sid, expected["source_url"]):
            expected, reason = None, "read_not_linked_to_own_authorization"
        chains.append((label, entry, expected, reason))
    failures = []
    ok = [c[2] for c in chains if c[2] is not None]
    if len(ok) == 2:
        a, b = ok
        for key, name in (("search_execution_id", "search_execution"), ("read_execution_id", "read_execution"),
                          ("source_url", "canonical_url"), ("title", "title"), ("deep_fact", "deep_fact")):
            if _norm(a[key]) == _norm(b[key]):
                failures.append(f"chains_share_{name}")
    return chains, failures


def make_v15_verifier(ledger_getter: Callable[[], Sequence[Mapping[str, Any]]], events_getter: Callable[[], Iterable[Mapping[str, Any]]],
                      search_effects_getter: Callable[[], Sequence[Any]], bindings_getter: Callable[[], Sequence[Any]],
                      deep_facts_getter: Callable[[], Sequence[Any]], read_effects_getter: Callable[[], Sequence[Any]]
                      ) -> Callable[[ComputerPageObservation], VerificationResult]:
    def verify(observation: ComputerPageObservation) -> VerificationResult:
        ledger = list(ledger_getter())
        facts: dict = {"SEARCH_CHAINS": len(ledger), "CHAINS_DISTINCT": False}
        for label in CHAIN_LABELS:
            facts[f"CHAIN_{label}_CORRELATED"] = False
            facts[f"CHAIN_{label}_VERIFIED"] = False
        if len(ledger) != 2:
            return VerificationResult(False, facts, ("exactly_two_search_chains_required",), (), ("two distinct sources researched",))
        events = list(events_getter())
        chains, failures = chain_expectations(ledger, events, search_effects_getter(), bindings_getter(), deep_facts_getter(),
                                              read_effects_getter())
        reasons: list = []
        satisfied: list = []
        missing: list = []
        for label, entry, expected, reason in chains:
            facts[f"CHAIN_{label}_CORRELATED"] = expected is not None
            if expected is None:
                reasons.append(f"chain_{label.lower()}:{reason}")
            sid = entry.get("execution_id")
            sub = make_v14_verifier(lambda e=entry: [e], lambda: events, search_effects_getter,
                                    lambda s=sid: _scoped(s, bindings_getter()), lambda s=sid: _scoped(s, deep_facts_getter()),
                                    read_effects_getter)(observation)
            facts[f"CHAIN_{label}_VERIFIED"] = bool(sub.verified)
            reasons += [f"chain_{label.lower()}:{r}" for r in sub.reasons]
            satisfied += [f"[{label}] {t}" for t in sub.satisfied]
            missing += [f"[{label}] {t}" for t in sub.missing]
        facts["CHAINS_DISTINCT"] = not failures and all(c[2] is not None for c in chains)
        reasons += failures
        verified = all(facts[f"CHAIN_{l}_VERIFIED"] for l in CHAIN_LABELS) and facts["CHAINS_DISTINCT"] and not reasons
        return VerificationResult(verified, facts, tuple(reasons), tuple(satisfied), tuple(missing))

    return verify
