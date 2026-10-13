"""Deterministic, 8B-specific artifact check over one fresh observation.

Deliberately not a generic artifact verifier. It judges ONLY the subtree of the single
preview iframe (the iframe that is the sibling of the editor's Publish/Upgrade controls).
Text anywhere else (composer, chat log, the "Choose Design" gallery, editor chrome) never
counts, and there is no global-search fallback: an ambiguous or missing preview region fails.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

from application.agent_loop import VerificationResult
from application.computer_runtime import ComputerPageObservation
from application.todomvc_verifier import _Node, _norm, _walk

TITLE = "IAOS_BROWSER_TOOL_OK"
SUBTITLE = "Artifact produced through governed browser interaction."
BUTTON = "Testar"
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_LINE = re.compile(r"^(\s*)- (.*)$")


def _split_entry(text: str) -> Tuple[str, Optional[str]]:
    """Split ``key: scalar`` at the first ``: `` that is outside quotes and brackets (or a trailing ``:``)."""
    in_quote, depth, i = False, 0, 0
    while i < len(text):
        ch = text[i]
        if in_quote:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_quote = False
        elif ch == '"':
            in_quote = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0 and (i + 1 == len(text) or text[i + 1] == " "):
            return text[:i], (text[i + 2:] if i + 1 < len(text) else None)
        i += 1
    return text, None


def parse_aria_tree(aria: str) -> List[_Node]:
    """Tolerant, indentation-based reader of the Playwright aria text.

    The real format is not always valid YAML (for example a bare ``=`` or ``@`` scalar inside an
    unrelated iframe), and one such line must not make the whole page unreadable.
    """
    root = _Node("<root>", None, [])
    stack: List[Tuple[int, _Node]] = [(-1, root)]
    for raw in aria.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        indent, text = len(m.group(1)), m.group(2).rstrip()
        key, scalar = _split_entry(text)
        if scalar is not None:
            scalar = scalar.strip()
            if len(scalar) >= 2 and scalar[0] == scalar[-1] == '"':
                try:
                    scalar = json.loads(scalar)
                except ValueError:
                    scalar = scalar[1:-1]
            if scalar == "":
                scalar = None
        node = _Node(key, scalar, [])
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack[-1][1].children.append(node)
        stack.append((indent, node))
    return root.children


def _own_text(node: _Node) -> str:
    """The node's accessible name/text: quoted name in the key, else its scalar, else bare-string key."""
    quoted = _QUOTED.search(node.key)
    if quoted:
        return _norm(quoted.group(1))
    if node.scalar:
        return _norm(node.scalar)
    return ""


def _name(node: _Node) -> str:
    text = _own_text(node)
    if text:
        return text
    return _norm(" ".join(_own_text(n) for n in _walk(node.children) if _own_text(n)))


def preview_regions(tree: List[_Node]) -> List[_Node]:
    """Every iframe that is a sibling of the editor chrome, i.e. of the container whose direct children
    include both a Publish and an Upgrade button (real structure: chrome and preview share a parent)."""
    found: List[_Node] = []
    for parent_children in [n.children for n in _walk(tree)] + [tree]:
        for chrome in parent_children:
            names = {_name(n).casefold() for n in chrome.children if n.role == "button"}
            if "publish" in names and "upgrade" in names:
                found.extend(n for n in parent_children if n.role == "iframe" and not any(n is f for f in found))
    return found


def verify_eightb_artifact(observation: ComputerPageObservation) -> VerificationResult:
    try:
        tree = parse_aria_tree(observation.aria_snapshot)
    except Exception:
        return VerificationResult(False, {}, ("aria_unreadable",))
    regions = preview_regions(tree)
    facts = {"PREVIEW_CANDIDATES": len(regions), "TITLE": False, "SUBTITLE": False, "BUTTON": False}
    if len(regions) != 1:
        return VerificationResult(False, facts, ("PREVIEW_REGION_AMBIGUOUS" if regions else "PREVIEW_REGION_MISSING",))
    inside = list(_walk(regions[0].children))
    facts["TITLE"] = any(n.role == "heading" and _name(n) == TITLE for n in inside)
    facts["SUBTITLE"] = any(n.role != "heading" and _own_text(n) == SUBTITLE for n in inside)
    facts["BUTTON"] = any(n.role == "button" and _name(n) == BUTTON for n in inside)
    reasons = tuple(k.lower() + "_missing" for k in ("TITLE", "SUBTITLE", "BUTTON") if not facts[k])
    return VerificationResult(not reasons, facts, reasons)


def summarize_eightb(observation: ComputerPageObservation) -> str:
    """One log line per observation (facts only; never page free text)."""
    try:
        tree = parse_aria_tree(observation.aria_snapshot)
        regions = preview_regions(tree)
        facts = verify_eightb_artifact(observation).facts
        dialog = any(n.role == "dialog" and "choose design" in _name(n).casefold() for n in _walk(tree))
        heads = [_name(n) for r in regions[:1] for n in _walk(r.children) if n.role == "heading"][:3]
        return f"url_path={observation.page_url.split('/')[3] if observation.page_url.count('/') >= 3 else ''} choose_design_dialog={dialog} facts={facts} preview_headings={heads}"
    except Exception:
        return "summary_unreadable"
