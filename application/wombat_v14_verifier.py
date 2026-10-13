"""V1.4 deep-read verifier: composition over the FROZEN V1.3 verifier (nothing in wombat_verifier / wombat_v13_verifier changes).

PASS requires, inside the single preview iframe only:
  * the exact title and the exact URL of the governed ``web.search`` result      (V1.3 source correspondence), and
  * the exact deep fact: the sentence extracted, by a deterministic rule, from the visible text of the governed DEEP READ of
    that same source (read execution correlated to the search binding, the evidence and the executor effect).
Expected values are never constants. A page that shows a correct-looking sentence without the governed read behind it fails.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from application.agent_loop import VerificationResult
from application.computer_runtime import ComputerPageObservation
from application.eightb_verifier import parse_aria_tree
from application.todomvc_verifier import _walk
from application.wombat_v13_verifier import _node_has, correlated_expected, make_v13_verifier
from application.wombat_verifier import _chrome_parents, preview_regions
from governance.bound_read import DEEP_READ_AUTHORITY, DEEP_READ_PROVENANCE, READ_SEARCH_SOURCE_ACTION, canonical_source_url

# The ONE extraction rule (narrow, auditable): the sentence of the source page that lists the RFCs that updated the document.
# Whitespace (incl. NBSP) is normalised first; exactly one distinct match must exist, otherwise there is no deep fact.
FACT_RULE = re.compile(r"This RFC was updated, see RFC \d+(?:, RFC \d+)*\.")

LABELS = {"SEARCH_RESULT_TITLE_EXACT": "exact title of the search result you obtained, shown in the artifact preview",
          "SEARCH_RESULT_URL_EXACT": "exact URL of the search result you obtained, shown in the artifact preview",
          "DEEP_FACT_EXACT": "exact sentence from the page of that source which says which RFCs updated the document, "
                             "shown in the artifact preview"}


def _norm(text: object) -> str:
    return " ".join(str(text).split())


def extract_deep_fact(visible_text: str) -> Optional[str]:
    found = set(FACT_RULE.findall(_norm(visible_text)))
    return next(iter(found)) if len(found) == 1 else None


def deep_correlation(ledger: Sequence[Mapping[str, Any]], events: Iterable[Mapping[str, Any]], search_effects: Sequence[Any],
                     bindings: Sequence[Any], deep_facts: Sequence[Any], read_effects: Sequence[Any]
                     ) -> tuple[Optional[Mapping[str, Any]], str]:
    """search execution -> SearchSourceBinding -> read execution -> DeepReadFact -> deep fact. (expected, reason)."""
    events = list(events)
    search = correlated_expected(ledger, events, search_effects)
    if search is None:
        return None, "no_correlated_search_execution"
    sid = search["execution_id"]
    try:
        bound_url = canonical_source_url(search["url"])
    except ValueError:
        return None, "search_url_not_readable"
    binding = next((b for b in bindings if b.search_execution_id == sid and b.effect_digest == search["result_digest"]
                    and b.canonical_url == bound_url), None)
    if binding is None:
        return None, "no_search_binding"
    fact = next((f for f in reversed(list(deep_facts)) if f.search_execution_id == sid and f.source_url == binding.canonical_url
                 and f.source_kind == "search_bound_deep_read"), None)
    if fact is None:
        return None, "no_deep_read_fact"
    executed = any(e.get("kind") == "EXECUTION_RESULT" and e.get("action") == READ_SEARCH_SOURCE_ACTION and e.get("ok") is True
                   and e.get("execution_id") == fact.read_execution_id and e.get("result_authority") == DEEP_READ_AUTHORITY
                   and e.get("result_provenance") == DEEP_READ_PROVENANCE for e in events)
    if not executed:
        return None, "read_execution_not_in_evidence"
    if not any(getattr(r, "opcode", None) == READ_SEARCH_SOURCE_ACTION and getattr(r, "result_digest", None) == fact.result_digest
               for r in read_effects):
        return None, "read_effect_not_correlated"
    if not any(e.get("kind") == "AUTHORIZATION" and e.get("action") == READ_SEARCH_SOURCE_ACTION and _binds(e, sid) for e in events):
        return None, "read_authorization_not_bound_to_search"
    deep = extract_deep_fact(fact.visible_text_excerpt)
    if deep is None:
        return None, "deep_fact_extraction_failed"
    return {"search_execution_id": sid, "read_execution_id": fact.read_execution_id, "title": search["title"], "url": search["url"],
            "deep_fact": deep, "source_url": fact.source_url, "truncated": fact.truncated,
            "search_effect_digest": search["result_digest"], "read_effect_digest": fact.result_digest}, "ok"


def _binds(event: Mapping[str, Any], search_execution_id: str) -> bool:
    for pair in event.get("args") or ():
        try:
            if pair[0] == "binding" and json.loads(pair[1]).get("search_execution_id") == search_execution_id:
                return True
        except (ValueError, TypeError, IndexError, AttributeError):
            continue
    return False


def make_v14_verifier(ledger_getter: Callable[[], Sequence[Mapping[str, Any]]], events_getter: Callable[[], Iterable[Mapping[str, Any]]],
                      search_effects_getter: Callable[[], Sequence[Any]], bindings_getter: Callable[[], Sequence[Any]],
                      deep_facts_getter: Callable[[], Sequence[Any]], read_effects_getter: Callable[[], Sequence[Any]]
                      ) -> Callable[[ComputerPageObservation], VerificationResult]:
    v13 = make_v13_verifier(ledger_getter, events_getter, search_effects_getter)

    def verify(observation: ComputerPageObservation) -> VerificationResult:
        facts = {"PREVIEW_CANDIDATES": 0, "SEARCH_CORRELATED": False, "DEEP_READ_CORRELATED": False, "SEARCH_RESULT_TITLE_EXACT": False,
                 "SEARCH_RESULT_URL_EXACT": False, "DEEP_FACT_EXACT": False, "OUTSIDE_PREVIEW_MATCHES_IGNORED": False}
        events = list(events_getter())
        expected, reason = deep_correlation(ledger_getter(), events, search_effects_getter(), bindings_getter(),
                                            deep_facts_getter(), read_effects_getter())
        facts["SEARCH_CORRELATED"] = correlated_expected(ledger_getter(), events, search_effects_getter()) is not None
        facts["DEEP_READ_CORRELATED"] = expected is not None
        if expected is None:
            return VerificationResult(False, facts, (reason,), (), tuple(LABELS.values()))
        base = v13(observation)                       # frozen structural + title/url judgement
        facts.update({k: base.facts.get(k, facts[k]) for k in ("PREVIEW_CANDIDATES", "SEARCH_RESULT_TITLE_EXACT", "SEARCH_RESULT_URL_EXACT")})
        try:
            tree = parse_aria_tree(observation.aria_snapshot)
        except Exception:
            return VerificationResult(False, facts, ("aria_unreadable",))
        chromes = sum(1 for _ in _chrome_parents(tree))
        regions = preview_regions(tree)
        if chromes != 1 or len(regions) != 1:
            return VerificationResult(False, facts, base.reasons or ("PREVIEW_REGION_MISSING",), (), ("a single identifiable artifact preview",))
        inside = list(_walk(regions[0].children))
        deep = expected["deep_fact"]
        facts["DEEP_FACT_EXACT"] = _node_has(inside, deep)
        outside = [n for n in _walk(tree) if not any(n is i for i in inside) and n is not regions[0]]
        facts["OUTSIDE_PREVIEW_MATCHES_IGNORED"] = bool(base.facts.get("OUTSIDE_PREVIEW_MATCHES_IGNORED")) or _node_has(outside, deep)
        reasons = tuple(k.lower() + "_missing" for k in LABELS if not facts[k])
        return VerificationResult(not reasons, facts, reasons, tuple(v for k, v in LABELS.items() if facts[k]),
                                  tuple(v for k, v in LABELS.items() if not facts[k]))

    return verify


def _has(text: str, value: str) -> bool:
    return bool(value) and _norm(value).casefold() in _norm(text).casefold()


def check_deep_fact_provenance(deep_fact: str, search_item: Mapping[str, Any], goal_text: str, first_prompt: str,
                               first_observation_text: str) -> dict:
    report = {}
    for name, text in (("SEARCH_TITLE", search_item.get("title", "")), ("SEARCH_URL", search_item.get("url", "")),
                       ("SEARCH_DESCRIPTION", search_item.get("description", "")), ("GOAL", goal_text),
                       ("INITIAL_PROMPT", first_prompt), ("INITIAL_OBSERVATION", first_observation_text)):
        report[f"DEEP_FACT_NOT_IN_{name}"] = "NO" if _has(str(text), deep_fact) else "YES"
    return report


def deep_preflight(*, result_count: int, search_url: str, bound_url: str, final_url: str, challenge: Optional[str],
                   takeover_requests: int, truncated: bool, visible_text: str, read_admitted: bool, fact_created: bool,
                   search_item: Mapping[str, Any], goal_text: str, first_prompt: str, first_observation_text: str) -> dict:
    """PREFLIGHT=OK only if every condition holds; otherwise ABORT with the failing keys. No policy is adapted."""
    try:
        search_c, bound_c, final_c = (canonical_source_url(u) for u in (search_url, bound_url, final_url))
    except ValueError:
        search_c = bound_c = final_c = ""
    deep = extract_deep_fact(visible_text)
    r = {"SEARCH_RESULT_COUNT": result_count, "BOUND_URL_EQUALS_SEARCH_URL": "YES" if search_c and search_c == bound_c else "NO",
         "FINAL_CANONICAL_URL_EQUALS_BOUND": "YES" if bound_c and final_c == bound_c else "NO",
         "CHALLENGE_PRESENT": "YES" if challenge else "NO", "TAKEOVER_REQUESTS": takeover_requests,
         "VISIBLE_TEXT_TRUNCATED": "YES" if truncated else "NO",
         "READ_RESULT_ADMITTED": "YES" if read_admitted else "NO", "DEEP_READ_FACT_CREATED": "YES" if fact_created else "NO",
         "DEEP_FACT_EXTRACTION": "PASS" if deep else "FAIL", "DEEP_FACT_IN_VISIBLE_TEXT": "YES" if deep else "NO",
         "DEEP_FACT_TRUNCATED_AWAY": "YES" if (truncated and not deep) else "NO"}
    if deep:
        r.update(check_deep_fact_provenance(deep, search_item, goal_text, first_prompt, first_observation_text))
        r["DEEP_FACT_IN_SEARCH_SNIPPET"] = "NO" if all(r[f"DEEP_FACT_NOT_IN_{k}"] == "YES" for k in
                                                      ("SEARCH_TITLE", "SEARCH_URL", "SEARCH_DESCRIPTION")) else "YES"
    failing = [k for k, bad in (("SEARCH_RESULT_COUNT", result_count != 1), ("BOUND_URL_EQUALS_SEARCH_URL", r["BOUND_URL_EQUALS_SEARCH_URL"] == "NO"),
                                ("FINAL_CANONICAL_URL_EQUALS_BOUND", r["FINAL_CANONICAL_URL_EQUALS_BOUND"] == "NO"),
                                ("CHALLENGE_PRESENT", bool(challenge)), ("TAKEOVER_REQUESTS", takeover_requests != 0),
                                ("READ_RESULT_ADMITTED", not read_admitted), ("DEEP_READ_FACT_CREATED", not fact_created),
                                ("DEEP_FACT_EXTRACTION", deep is None))
               if bad]
    failing += [k for k, v in r.items() if k.startswith("DEEP_FACT_NOT_IN_") and v == "NO"]
    r["PREFLIGHT"] = "ABORT" if failing else "OK"
    r["PREFLIGHT_FAILING"] = failing
    r["DEEP_FACT"] = deep
    return r
