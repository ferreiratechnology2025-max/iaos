from governance.durable_tool_state import SQLiteToolStateStore
from governance.tool_invocation import arguments_digest
from governance.tool_kernel import ToolInvocationKernel
import hashlib
import json


def invocation():
    return {
        "tool_name": "create_space_page",
        "tool_namespace": "opendots",
        "arguments_digest": arguments_digest({"title": "durable"}),
        "resource": "space/durable",
        "actor": "agent",
        "context": "restart-test",
        "effect_class": "EFFECT",
    }


def observation():
    return {
        "tool_name": "search_web",
        "tool_namespace": "opendots",
        "arguments_digest": arguments_digest({"objective": "mission", "search_queries": ["mission"]}),
        "resource": "research/thread-1",
        "actor": "agent",
        "context": "thread-1",
        "effect_class": "OBSERVE",
        "evidence_mode": "STRUCTURED",
    }


def result_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def test_authorization_and_consumption_survive_restart(tmp_path):
    path = tmp_path / "tool-state.sqlite"
    first = ToolInvocationKernel(durable_store=SQLiteToolStateStore(path))
    auth = first.authorize_and_consume(invocation(), allow_effects=True)

    restarted = SQLiteToolStateStore(path)
    record = restarted.authorization(auth["authorization_id"])
    assert record["consumed_execution_id"] == auth["execution_id"]
    assert restarted.consume_existing(auth["authorization_id"], "second-execution") is False


def test_receipt_and_evidence_survive_restart_with_binding(tmp_path):
    path = tmp_path / "tool-state.sqlite"
    first = ToolInvocationKernel(durable_store=SQLiteToolStateStore(path))
    auth = first.authorize_and_consume(invocation(), allow_effects=True)
    first.record_receipt({**invocation(), **auth, "result_identity": {"id": "page-1", "revision": 1,
                                                                      "spaceId": "durable"}})

    restarted = SQLiteToolStateStore(path)
    receipt = restarted.receipt(auth["authorization_id"])
    assert receipt["execution_id"] == auth["execution_id"]
    assert receipt["tool_name"] == "create_space_page"
    assert receipt["resource"] == "space/durable"
    assert receipt["arguments_digest"] == invocation()["arguments_digest"]
    assert receipt["result_identity"] == {"id": "page-1", "revision": 1, "spaceId": "durable"}
    assert [event["kind"] for event in restarted.evidence_events()] == ["AUTHORIZATION", "EXECUTION_RESULT"]


def test_missing_receipt_after_restart_is_not_reexecution_authority(tmp_path):
    path = tmp_path / "tool-state.sqlite"
    first = ToolInvocationKernel(durable_store=SQLiteToolStateStore(path))
    auth = first.authorize_and_consume(invocation(), allow_effects=True)

    restarted = SQLiteToolStateStore(path)
    record = restarted.authorization(auth["authorization_id"])
    assert record["consumed_execution_id"] == auth["execution_id"]
    assert record["receipt_status"] == "UNKNOWN_OR_INCOMPLETE"
    assert restarted.consume_existing(auth["authorization_id"], "retry-execution") is False


def test_incomplete_receipt_does_not_authorize_automatic_effect_retry(tmp_path):
    path = tmp_path / "tool-state.sqlite"
    first = ToolInvocationKernel(durable_store=SQLiteToolStateStore(path))
    auth = first.authorize_and_consume(invocation(), allow_effects=True)
    executor_calls = []
    executor_calls.append("effect-once")

    restarted = SQLiteToolStateStore(path)
    assert restarted.authorization(auth["authorization_id"])["receipt_status"] == "UNKNOWN_OR_INCOMPLETE"
    assert restarted.consume_existing(auth["authorization_id"], "automatic-retry") is False
    assert executor_calls == ["effect-once"]


def test_structured_observation_receipt_preserves_sources_across_restart(tmp_path):
    path = tmp_path / "tool-state.sqlite"
    source_result = {"sources": [
        {"url": "https://nasa.gov/a", "title": "A", "text": "NASA evidence"},
        {"url": "https://esa.int/b", "title": "B", "text": "ESA evidence"},
    ]}
    first = ToolInvocationKernel(durable_store=SQLiteToolStateStore(path))
    auth = first.authorize_and_consume(observation(), allow_effects=True)
    assert auth["status"] == "AUTHORIZED" or auth["status"] == "MEDIATED"
    first.record_receipt({**observation(), **auth,
                          "result_identity": {},
                          "result_digest": result_digest(source_result),
                          "structured_evidence": source_result})

    restarted = SQLiteToolStateStore(path)
    receipt = restarted.receipt_by_invocation(auth["invocation_id"])
    assert receipt["structured_evidence"] == source_result
    assert receipt["result_digest"] == result_digest(source_result)


def test_structured_receipt_rejects_changed_result_digest(tmp_path):
    store = SQLiteToolStateStore(tmp_path / "tool-state.sqlite")
    kernel = ToolInvocationKernel(durable_store=store)
    auth = kernel.authorize_and_consume(observation(), allow_effects=True)
    try:
        kernel.record_receipt({**observation(), **auth, "result_identity": {},
                               "result_digest": "0" * 64,
                               "structured_evidence": {"sources": []}})
    except ValueError as exc:
        assert str(exc) == "structured_evidence_digest_mismatch"
    else:
        raise AssertionError("changed result digest must be rejected")
