import json

from application.action_schema import decision_rejection_feedback
from application.computer_runtime import ComputerElement, ComputerPageObservation


def observation():
    return ComputerPageObservation(
        "https://example.test/", "Example", "session", 1,
        (ComputerElement("f115e17", "link", "Start building"),), "",
    )


def test_schema_feedback_identifies_key_path_value_and_typed_ref_semantics():
    raw = json.dumps({"kind": "act", "action": "browser.key", "args": {"key": "f115e17"}})
    feedback = decision_rejection_feedback("invalid_model_action_schema", raw, observation())
    assert "Action: browser.key" in feedback
    assert "args.key" in feedback
    assert "f115e17" in feedback
    assert "element ref" in feedback
    assert "keyboard keys" in feedback


def test_schema_feedback_does_not_use_ref_format_heuristics():
    raw = json.dumps({"kind": "act", "action": "browser.key", "args": {"key": "f115e17"}})
    no_match = ComputerPageObservation("https://example.test/", "Example", "session", 1, (), "")
    feedback = decision_rejection_feedback("invalid_model_action_schema", raw, no_match)
    assert "element ref" not in feedback
    assert "canonical keyboard-key schema" in feedback


def test_invalid_json_feedback_requires_bare_json_without_stripping_input():
    feedback = decision_rejection_feedback("invalid_json", "```json {} ```", observation())
    assert "exactly one bare JSON object" in feedback
    assert "no markdown or code fences" in feedback
