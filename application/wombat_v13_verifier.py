"""V1.3 cross-tool verifier: composition over the FROZEN Wombat structural helpers (nothing in wombat_verifier is changed).

PASS requires, inside the single preview iframe only:
  * the exact title returned by the governed ``web.search`` execution, and
  * the exact URL returned by that same execution,
and the expected values are NOT constants: they are taken from the harness ledger entry that is correlated with BOTH the
EXECUTION_RESULT evidence event (same execution_id, ok) AND the executor's recorded effect (same result digest).
Text found anywhere outside the preview (prompt box, editors, dialogs, other frames, SOURCE_DATA echoed by the page)
never counts: there is no global-search fallback.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from application.agent_loop import VerificationResult
from application.computer_runtime import ComputerPageObservation
from application.eightb_verifier import _name, _own_text, parse_aria_tree
from application.todomvc_verifier import _walk
from application.wombat_verifier import _chrome_parents, preview_regions

LABELS = {"SEARCH_RESULT_TITLE_EXACT": "exact title of the search result you obtained, shown in the artifact preview",
          "SEARCH_RESULT_URL_EXACT": "exact URL of the search result you obtained, shown in the artifact preview"}


def correlated_expected(ledger: Sequence[Mapping[str, Any]], events: Iterable[Mapping[str, Any]],
                        effects: Sequence[Any]) -> Optional[Mapping[str, Any]]:
    """Latest ledger entry proven by BOTH the execution evidence and the executor effect; otherwise None."""
    events = list(events)
    for entry in reversed(list(ledger)):
        executed = any(e.get("kind") == "EXECUTION_RESULT" and e.get("action") == "web.search" and e.get("ok") is True
                       and e.get("execution_id") == entry.get("execution_id") for e in events)
        effect = any(getattr(r, "opcode", None) == "web.search" and getattr(r, "result_digest", None) == entry.get("result_digest")
                     for r in effects)
        if executed and effect and entry.get("title") and entry.get("url"):
            return entry
    return None


def _node_has(nodes, value: str) -> bool:
    """The exact value occurs at least once inside the text of ONE node (whitespace-normalised; no semantic character is changed).

    Deliberately not an equality check: the goal asks for the value to be presented, not for it to sit in its own element,
    so title and URL may share a paragraph. Matching never spans two nodes, so a value cannot be assembled from fragments."""
    wanted = " ".join(value.split())
    return bool(wanted) and any(wanted in " ".join(_own_text(n).split()) for n in nodes)


def make_v13_verifier(ledger_getter: Callable[[], Sequence[Mapping[str, Any]]],
                      events_getter: Callable[[], Iterable[Mapping[str, Any]]],
                      effects_getter: Callable[[], Sequence[Any]]) -> Callable[[ComputerPageObservation], VerificationResult]:
    def verify(observation: ComputerPageObservation) -> VerificationResult:
        facts = {"PREVIEW_CANDIDATES": 0, "SEARCH_CORRELATED": False, "SEARCH_RESULT_TITLE_EXACT": False,
                 "SEARCH_RESULT_URL_EXACT": False, "OUTSIDE_PREVIEW_MATCHES_IGNORED": False}
        expected = correlated_expected(ledger_getter(), events_getter(), effects_getter())
        facts["SEARCH_CORRELATED"] = expected is not None
        if expected is None:
            return VerificationResult(False, facts, ("no_correlated_search_execution",), (), tuple(LABELS.values()))
        try:
            tree = parse_aria_tree(observation.aria_snapshot)
        except Exception:
            return VerificationResult(False, facts, ("aria_unreadable",))
        chromes = sum(1 for _ in _chrome_parents(tree))
        regions = preview_regions(tree)
        facts["PREVIEW_CANDIDATES"] = len(regions)
        if chromes != 1 or len(regions) != 1:
            reason = "PREVIEW_REGION_AMBIGUOUS" if chromes > 1 or len(regions) > 1 else "PREVIEW_REGION_MISSING"
            return VerificationResult(False, facts, (reason,), (), ("a single identifiable artifact preview",))
        inside = list(_walk(regions[0].children))
        title, url = str(expected["title"]), str(expected["url"])
        facts["SEARCH_RESULT_TITLE_EXACT"] = _node_has(inside, title)
        facts["SEARCH_RESULT_URL_EXACT"] = _node_has(inside, url)
        outside = [n for n in _walk(tree) if not any(n is i for i in inside) and n is not regions[0]]
        facts["OUTSIDE_PREVIEW_MATCHES_IGNORED"] = _node_has(outside, title) or _node_has(outside, url)
        reasons = tuple(k.lower().replace("search_result_", "search_result_") + "_missing" for k in LABELS if not facts[k])
        satisfied = tuple(v for k, v in LABELS.items() if facts[k])
        missing = tuple(v for k, v in LABELS.items() if not facts[k])
        return VerificationResult(not reasons, facts, reasons, satisfied, missing)

    return verify
