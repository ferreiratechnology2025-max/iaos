import json

import pytest

from application.action_schema import validate_model_action_schema
from application.browser_key_contract import browser_key_action_schema, canonical_browser_key_tokens
from cognitive.agent_contracts import DecisionParseError


def action(key):
    return json.dumps({"kind": "act", "action": "browser.key", "args": {"key": key}})


@pytest.mark.parametrize("key", ["Enter", "Tab", "ArrowDown", "Control+Enter", "a"])
def test_schema_admits_canonical_keys(key):
    validate_model_action_schema(action(key))


@pytest.mark.parametrize("key", ["f115e17", "f116e10", "Click"])
def test_schema_rejects_invalid_keys_before_action_admission(key):
    with pytest.raises(DecisionParseError, match="invalid_model_action_schema"):
        validate_model_action_schema(action(key))


def test_schema_is_machine_readable_and_derived_from_canonical_contract():
    schema = browser_key_action_schema()
    assert schema["type"] == "object"
    assert schema["properties"]["key"]["type"] == "string"
    assert schema["properties"]["key"]["maxLength"] == 64
    assert all(token in schema["properties"]["key"]["pattern"] for token in ("Enter", "Tab", "ArrowDown"))
    assert set(canonical_browser_key_tokens()) >= {"Enter", "Tab", "ArrowDown"}
