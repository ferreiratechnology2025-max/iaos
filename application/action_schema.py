"""Deterministic admission checks for raw model actions.

This layer protects the application/runtime boundary without changing the
cognitive decision contracts.  Runtime validators remain an independent
second defense at execution time.
"""
from __future__ import annotations

import json
from typing import Any

from application.browser_key_contract import is_valid_browser_key
from cognitive.agent_contracts import DecisionParseError, _no_duplicates


def validate_model_action_schema(text: object) -> None:
    """Reject structurally invalid browser-key actions before parser admission."""
    if not isinstance(text, str) or not text.strip():
        return
    try:
        data: Any = json.loads(text.strip(), object_pairs_hook=_no_duplicates)
    except (DecisionParseError, ValueError):
        return
    if not isinstance(data, dict) or data.get("kind") != "act":
        return
    if data.get("action") != "browser.key" or not isinstance(data.get("args"), dict):
        return
    key = data["args"].get("key")
    if isinstance(key, str) and is_valid_browser_key(key):
        return
    raise DecisionParseError("invalid_model_action_schema")


def decision_rejection_feedback(code: str, raw_text: object, observation: Any) -> str:
    """Build precise recovery guidance without repairing or reinterpreting actions."""
    if code == "invalid_json":
        return (
            "Decision rejected: invalid JSON. Return exactly one bare JSON object; "
            "no markdown or code fences."
        )
    if code != "invalid_model_action_schema":
        return f"Your previous reply was rejected ({code}). Reply with exactly one valid JSON object."

    action = None
    rejected_value = None
    try:
        data = json.loads(str(raw_text).strip(), object_pairs_hook=_no_duplicates)
        if isinstance(data, dict):
            action = data.get("action")
            args = data.get("args")
            if isinstance(args, dict):
                rejected_value = args.get("key")
    except (DecisionParseError, ValueError):
        pass

    suffix = "Return exactly one bare JSON decision; no markdown or code fences."
    message = (
        "Decision rejected before execution: invalid_model_action_schema. "
        f"Action: {action or 'unknown'}. Invalid field: args.key. "
        f"Rejected value: {rejected_value!r}. "
        "Reason: args.key violates the canonical keyboard-key schema. "
        + suffix
    )
    if action == "browser.key" and isinstance(rejected_value, str):
        elements = getattr(observation, "elements", ())
        matched = next((element for element in elements if getattr(element, "ref", None) == rejected_value), None)
        if matched is not None:
            message = (
                message.rsplit(suffix, 1)[0]
                + f"The rejected value is the observed element ref for a {matched.role} named {matched.name!r}; "
                "element refs are not keyboard keys. "
                "Use an element-targeting browser action when intending to interact with that ref. "
                + suffix
            )
    return message
