"""Opt-in ``browser.type_source_value``: the model names WHICH governed source value goes into WHICH control; it never supplies the characters.

The action is a semantic reference ``{ref, source, field}``. The trusted runtime resolves it from the source store and types the stored
value verbatim through the ordinary ``browser.type`` path. A literal ``text``/``value`` (or any other key) is rejected by the parser.
Without the opt-in the decision parser is exactly ``cognitive.agent_contracts.parse_decision``."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Collection

from cognitive.agent_contracts import (DecisionParseError, MAX_SOURCE_ORDINAL, AgentDecision, _no_duplicates, parse_decision)

TYPE_SOURCE_VALUE_ACTION = "browser.type_source_value"
SOURCE_VALUE_FIELDS = ("title", "url", "deep_fact")      # closed schema: no JSON paths, no free field names


@dataclass(frozen=True)
class TypeSourceValueDecision:
    """Type the verbatim value of one governed source field into one editable control. Carries no literal text."""
    ref: str
    source: int
    field: str


def parse_decision_with_source_value(text: object, known_refs: Collection[str] | None = None, allow_search: bool = False,
                                     allow_read: bool = False, allow_source_value: bool = False) -> AgentDecision | TypeSourceValueDecision:
    if allow_source_value and isinstance(text, str) and text.strip():
        try:
            data = json.loads(text.strip(), object_pairs_hook=_no_duplicates)
        except (DecisionParseError, ValueError):
            data = None          # the ordinary parser reports the precise code
        if isinstance(data, dict) and data.get("kind") == "act" and data.get("action") == TYPE_SOURCE_VALUE_ACTION:
            if set(data) != {"kind", "action", "args"}:
                raise DecisionParseError("unexpected_fields")
            args = data["args"]
            if not isinstance(args, dict):
                raise DecisionParseError("args_invalid")
            if set(args) != {"ref", "source", "field"}:        # a literal value (text/value/...) or a missing key is refused
                raise DecisionParseError("args_fields")
            ref = args["ref"]
            if not isinstance(ref, str) or not ref or len(ref) > 128:
                raise DecisionParseError("ref_invalid")
            if known_refs is not None and ref not in known_refs:
                raise DecisionParseError("unknown_ref")
            source = args["source"]
            if not isinstance(source, int) or isinstance(source, bool) or not 1 <= source <= MAX_SOURCE_ORDINAL:
                raise DecisionParseError("source_invalid")
            if args["field"] not in SOURCE_VALUE_FIELDS:
                raise DecisionParseError("field_invalid")
            return TypeSourceValueDecision(ref, source, args["field"])
    return parse_decision(text, known_refs, allow_search=allow_search, allow_read=allow_read)
