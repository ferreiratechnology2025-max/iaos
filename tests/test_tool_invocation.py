from governance.tool_invocation import arguments_digest, mediate_observation


def test_observation_is_generic_and_side_effect_free():
    payload = {
        "tool_name": "list_authorized_spaces",
        "tool_namespace": "opendots",
        "arguments_digest": arguments_digest({}),
        "resource": "tool/list_authorized_spaces",
        "actor": "agent",
        "context": "smoke",
        "effect_class": "OBSERVE",
    }
    assert mediate_observation(payload) == {
        "status": "MEDIATED",
        "decision": "ALLOW",
        "effect_class": "OBSERVE",
        "tool_name": "list_authorized_spaces",
        "arguments_digest": payload["arguments_digest"],
    }


def test_effects_are_not_authorized_by_gate_b1():
    payload = {
        "tool_name": "create_space_page",
        "tool_namespace": "opendots",
        "arguments_digest": arguments_digest({"title": "x"}),
        "resource": "space/test",
        "actor": "agent",
        "context": "smoke",
        "effect_class": "EFFECT",
    }
    result = mediate_observation(payload)
    assert result["status"] == "DENIED"
    assert result["decision"] == "DENY"
    assert result["reason"] == "B1_EFFECT_NOT_ENABLED"
