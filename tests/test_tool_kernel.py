import pytest

from governance.tool_invocation import arguments_digest
from governance.tool_kernel import ToolInvocationKernel


def invocation(**overrides):
    value = {
        "tool_name": "create_space_page",
        "tool_namespace": "opendots",
        "arguments_digest": arguments_digest({"title": "B2"}),
        "resource": "space/test",
        "actor": "agent",
        "context": "b2",
        "effect_class": "EFFECT",
    }
    value.update(overrides)
    return value


def test_authorize_consume_receipt_and_exact_binding():
    kernel = ToolInvocationKernel()
    auth = kernel.authorize_and_consume(invocation(), allow_effects=True)
    assert auth["decision"] == "ALLOW"
    receipt = kernel.record_receipt({**invocation(), **auth, "result_identity": {"id": "page-1", "revision": 1}})
    assert receipt["status"] == "RECEIPTED"
    assert [event["kind"] for event in kernel.evidence_events()] == ["AUTHORIZATION", "EXECUTION_RESULT"]


@pytest.mark.parametrize("field", ["tool_name", "resource", "arguments_digest"])
def test_binding_mutation_fails_closed(field):
    kernel = ToolInvocationKernel()
    auth = kernel.authorize_and_consume(invocation(), allow_effects=True)
    mutated = {**invocation(), **auth, "result_identity": {"id": "page-1"}}
    mutated[field] = "changed"
    with pytest.raises(ValueError, match="effect_binding_mismatch"):
        kernel.record_receipt(mutated)


def test_effect_is_denied_without_b2_mode_and_consumption_is_absent():
    kernel = ToolInvocationKernel()
    result = kernel.authorize_and_consume(invocation(), allow_effects=False)
    assert result["decision"] == "DENY"
    assert kernel.ledger.consumed_count() == 0
