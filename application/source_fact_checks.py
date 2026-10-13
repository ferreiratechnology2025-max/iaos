"""V1.3 provenance checks for a source fact: where it came from and where it was NOT present before the search.

Pure functions over data the harness already has. The expected value comes from the real governed ``web.search``
execution (its evidence and executor record), never from a constant supplied by the harness.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from application.source_facts import SourceFact


def fact_values(fact: SourceFact) -> tuple:
    return tuple(v for v in (fact.title, fact.url) if v)


def fact_in_text(fact: SourceFact, text: str) -> bool:
    """True when every non-empty fact value (title and url) appears in ``text`` (case-insensitive, whitespace-normalised)."""
    haystack = " ".join(text.split()).casefold()
    values = fact_values(fact)
    return bool(values) and all(" ".join(v.split()).casefold() in haystack for v in values)


def _absent(fact: SourceFact, text: str) -> str:
    haystack = " ".join(text.split()).casefold()
    return "NO" if any(" ".join(v.split()).casefold() in haystack for v in fact_values(fact)) else "YES"


def check_fact_provenance(fact: SourceFact, goal_text: str, first_prompt: str, first_observation_text: str,
                          events: Iterable[Mapping[str, Any]], effects: Sequence[Any]) -> dict:
    executed = any(
        e.get("kind") == "EXECUTION_RESULT" and e.get("action") == "web.search" and e.get("ok") is True
        and e.get("execution_id") == fact.source_id for e in events)
    from_result = any(getattr(r, "opcode", None) == "web.search" and getattr(r, "result_digest", None) == fact.result_digest
                      for r in effects)
    return {
        "EXPECTED_FACT_NOT_IN_GOAL": _absent(fact, goal_text),
        "EXPECTED_FACT_NOT_IN_INITIAL_PROMPT": _absent(fact, first_prompt),
        "EXPECTED_FACT_NOT_IN_INITIAL_OBSERVATION": _absent(fact, first_observation_text),
        "WEB_SEARCH_EXECUTED": "YES" if executed else "NO",
        "EXPECTED_FACT_FROM_SEARCH_RESULT": "YES" if from_result else "NO",
    }


def _contains(text: str, value: str) -> bool:
    return bool(value) and " ".join(value.split()).casefold() in " ".join(text.split()).casefold()


def check_title_url_provenance(title: str, url: str, goal_text: str, first_prompt: str, first_observation_text: str,
                               ledger: Sequence[Mapping[str, Any]], events: Iterable[Mapping[str, Any]],
                               effects: Sequence[Any]) -> dict:
    """Separate title/url provenance report. ``PREFLIGHT`` is ABORT when either value pre-exists in goal/prompt/observation."""
    from application.wombat_v13_verifier import correlated_expected
    expected = correlated_expected(ledger, events, effects)
    report = {}
    for name, text in (("GOAL", goal_text), ("INITIAL_PROMPT", first_prompt), ("INITIAL_OBSERVATION", first_observation_text)):
        report[f"EXPECTED_TITLE_NOT_IN_{name}"] = "NO" if _contains(text, title) else "YES"
        report[f"EXPECTED_URL_NOT_IN_{name}"] = "NO" if _contains(text, url) else "YES"
    report["EXPECTED_TITLE_FROM_GOVERNED_SEARCH_EFFECT"] = "YES" if expected and expected["title"] == title else "NO"
    report["EXPECTED_URL_FROM_GOVERNED_SEARCH_EFFECT"] = "YES" if expected and expected["url"] == url else "NO"
    pre = [k for k, v in report.items() if "_NOT_IN_" in k and v == "NO"]
    report["PREFLIGHT"] = "ABORT" if pre else "OK"
    return report