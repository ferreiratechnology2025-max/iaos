import pytest

from governance.tool_invocation import arguments_digest
from governance.tool_kernel import ToolInvocationKernel


def test_result_resource_mismatch_fails_closed():
    kernel = ToolInvocationKernel()
    invocation = {
        "tool_name": "create_space_page",
        "tool_namespace": "opendots",
        "arguments_digest": arguments_digest({"title": "B2"}),
        "resource": "space/authorized",
        "actor": "agent",
        "context": "b2",
        "effect_class": "EFFECT",
    }
    auth = kernel.authorize_and_consume(invocation, allow_effects=True)
    with pytest.raises(ValueError, match="effect_resource_mismatch"):
        kernel.record_receipt({**invocation, **auth, "result_identity": {"spaceId": "other", "id": "page"}})
