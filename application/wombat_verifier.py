"""Deterministic, Website-Wombat-specific artifact check over one fresh observation.

Same contract as the 8B verifier: it judges ONLY the subtree of the single preview iframe. The
prompt box, the Form/JSON editors, the "Start a new site" dialog and every other iframe never count,
and there is no global-search fallback: an ambiguous or missing preview region fails.

The preview iframe is located structurally, never by ref: the builder chrome is the node whose direct
children include the "Download", "Copy link" and "Edit link" buttons; the preview is an iframe inside a
sibling of that chrome node. Zero or several candidate regions fail.

Site-specific button semantics (evidence: docs/evidence/v1-2-wombat-button-rendering-diagnostic-v2.txt): the builder's
"Button" block is rendered inside the preview as an anchor, i.e. ``link "Testar"``. The button criterion is therefore the
exact accessible name with role in {button, link}, and only inside the unique preview subtree. This is not a general
relaxation: a paragraph, heading or any other role with the same text never counts.
"""
from __future__ import annotations

from typing import List

from application.agent_loop import VerificationResult
from application.computer_runtime import ComputerPageObservation
from application.eightb_verifier import BUTTON, SUBTITLE, TITLE, _name, _own_text, parse_aria_tree
from application.todomvc_verifier import _Node, _walk

CHROME_BUTTONS = frozenset({"download", "copy link", "edit link"})
BUTTON_ROLES = ("button", "link")   # Wombat renders its Button block as <a>


def _chrome_parents(tree: List[_Node]):
    """Yield (siblings, chrome) for every node whose direct children include all chrome buttons."""
    for siblings in [n.children for n in _walk(tree)] + [tree]:
        for chrome in siblings:
            names = {_name(n).casefold() for n in chrome.children if n.role == "button"}
            if CHROME_BUTTONS <= names:
                yield siblings, chrome


def preview_regions(tree: List[_Node]) -> List[_Node]:
    found: List[_Node] = []
    for siblings, chrome in _chrome_parents(tree):
        for sibling in siblings:
            if sibling is chrome:
                continue
            for n in _walk([sibling]):
                if n.role == "iframe" and not any(n is f for f in found):
                    found.append(n)
    return found


def verify_wombat_artifact(observation: ComputerPageObservation) -> VerificationResult:
    try:
        tree = parse_aria_tree(observation.aria_snapshot)
    except Exception:
        return VerificationResult(False, {}, ("aria_unreadable",))
    chromes = sum(1 for _ in _chrome_parents(tree))
    regions = preview_regions(tree)
    facts = {"PREVIEW_CANDIDATES": len(regions), "TITLE": False, "SUBTITLE": False, "BUTTON": False}
    if chromes != 1 or len(regions) != 1:
        return VerificationResult(False, facts, ("PREVIEW_REGION_AMBIGUOUS" if chromes > 1 or len(regions) > 1 else "PREVIEW_REGION_MISSING",),
                                  (), ("a single identifiable artifact preview",))
    inside = list(_walk(regions[0].children))
    facts["TITLE"] = any(n.role == "heading" and _name(n) == TITLE for n in inside)
    facts["SUBTITLE"] = any(n.role != "heading" and _own_text(n) == SUBTITLE for n in inside)
    facts["BUTTON"] = any(n.role in BUTTON_ROLES and _name(n) == BUTTON for n in inside)
    reasons = tuple(k.lower() + "_missing" for k in ("TITLE", "SUBTITLE", "BUTTON") if not facts[k])
    labels = {"TITLE": "main title exact as heading", "SUBTITLE": "subtitle exact",
              "BUTTON": "Testar control in artifact preview"}
    satisfied = tuple(v for k, v in labels.items() if facts[k])
    missing = tuple(v for k, v in labels.items() if not facts[k])
    return VerificationResult(not reasons, facts, reasons, satisfied, missing)


def summarize_wombat(observation: ComputerPageObservation) -> str:
    """One log line per observation (facts only; never page free text)."""
    try:
        tree = parse_aria_tree(observation.aria_snapshot)
        regions = preview_regions(tree)
        facts = verify_wombat_artifact(observation).facts
        modal = any(n.role == "heading" and _name(n).casefold() == "start a new site" for n in _walk(tree))
        heads = [_name(n) for r in regions[:1] for n in _walk(r.children) if n.role == "heading"][:3]
        path = observation.page_url.split("/", 3)[3] if observation.page_url.count("/") >= 3 else ""
        return f"url_path=/{path} start_new_site_dialog={modal} facts={facts} preview_headings={heads}"
    except Exception:
        return "summary_unreadable"
