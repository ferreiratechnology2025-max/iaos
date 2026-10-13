"""Deterministic, TodoMVC-specific success check over one fresh observation.

Deliberately not a generic criteria engine. Judges only the observation it is
given, never the agent's history or claim.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import yaml

from application.agent_loop import VerificationResult
from application.computer_runtime import ComputerPageObservation

EXPECTED = (("comprar café", True), ("ligar para Maria", False))
EXPECTED_ITEMS_LEFT = 1
# Real demo.playwright.dev aria renders the (hover-only) delete button as a bare text node.
_DELETE_GLYPHS = {"×", "✖"}
_ROLE = re.compile(r"^\s*([A-Za-z][\w-]*)")
_ITEMS_LEFT = re.compile(r"(\d+)\s+items?\s+left", re.IGNORECASE)


@dataclass
class _Node:
    key: str
    scalar: Optional[str]
    children: List["_Node"]

    @property
    def role(self) -> str:
        m = _ROLE.match(self.key)
        return m.group(1).lower() if m else ""


def _nodes(value: Any) -> List[_Node]:
    out: List[_Node] = []
    if isinstance(value, list):
        for item in value:
            out.extend(_nodes(item))
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (list, dict)):
                out.append(_Node(str(key), None, _nodes(child)))
            else:
                out.append(_Node(str(key), None if child is None else str(child), []))
    elif value is not None:
        # A bare string entry in the aria tree is a node (``checkbox "x" [checked]``), not text.
        out.append(_Node(str(value), None, []))
    return out


def _walk(nodes: List[_Node]):
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _checked(key: str) -> bool:
    return bool(re.search(r"\[checked(=true)?\]", key))


def _item_text(item: _Node) -> str:
    parts: List[str] = []
    for node in _walk(item.children):
        if node.role in {"checkbox", "button"}:
            continue
        if node.scalar and node.scalar.strip() not in _DELETE_GLYPHS:
            parts.append(node.scalar)
    return _norm(" ".join(parts))


def parse_todos(aria: str) -> Tuple[List[Tuple[str, bool]], Optional[int]]:
    tree = _nodes(yaml.safe_load(aria))
    todos: List[Tuple[str, bool]] = []
    inside: set = set()
    for node in _walk(tree):
        if node.role == "listitem":
            boxes = [n for n in _walk(node.children) if n.role == "checkbox"]
            if len(boxes) != 1:
                continue
            todos.append((_item_text(node), _checked(boxes[0].key)))
            inside.update(id(n) for n in _walk(node.children))
            inside.add(id(node))
    outside = " ".join(n.scalar for n in _walk(tree) if id(n) not in inside and n.scalar)
    match = _ITEMS_LEFT.search(_norm(outside))
    return todos, int(match.group(1)) if match else None


def verify_todomvc(observation: ComputerPageObservation) -> VerificationResult:
    try:
        todos, left = parse_todos(observation.aria_snapshot)
    except Exception:
        return VerificationResult(False, {}, ("aria_unreadable",))
    facts = {
        "TOTAL_TODOS": len(todos),
        "CAFE_CHECKED": _state(todos, EXPECTED[0][0]),
        "MARIA_CHECKED": _state(todos, EXPECTED[1][0]),
        "ITEMS_LEFT": left,
    }
    reasons: List[str] = []
    if len(todos) != len(EXPECTED):
        reasons.append("total_todos")
    for text, want in EXPECTED:
        matches = [c for t, c in todos if t == _norm(text)]
        if len(matches) != 1:
            reasons.append(f"not_exactly_once:{text}")
        elif matches[0] is not want:
            reasons.append(f"wrong_checked_state:{text}")
    if left != EXPECTED_ITEMS_LEFT:
        reasons.append("items_left")
    return VerificationResult(not reasons, facts, tuple(reasons))


def _state(todos, text):
    matches = [c for t, c in todos if t == _norm(text)]
    return matches[0] if len(matches) == 1 else None


def summarize_todomvc(observation: ComputerPageObservation) -> str:
    """Audit line: what the model could see. Reports the input value from BOTH prompt sources."""
    try:
        todos, left = parse_todos(observation.aria_snapshot)
        todo_part = [f"{t}{'[x]' if c else '[ ]'}" for t, c in todos]
    except Exception:
        todo_part, left = "unparseable", None
    box = next((e for e in observation.elements if e.role == "textbox"), None)
    from_elements = box.value if box is not None and box.value is not None else ""
    try:
        node = next((n for n in _walk(_nodes(yaml.safe_load(observation.aria_snapshot))) if n.role == "textbox"), None)
        from_aria = node.scalar if node is not None and node.scalar else ""
    except Exception:
        from_aria = "?"
    return f"todos={todo_part} input_value(elements)={from_elements!r} input_value(aria)={from_aria!r} items_left={left}"
