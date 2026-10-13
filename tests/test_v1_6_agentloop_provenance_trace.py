from application.execution_trace import MemoryExecutionTrace, redact_sensitive_text
from scripts.agent_opendots_v16_live import DurableRunJournal, DurableTraceSink, read_durable_run_journal


def test_provenance_trace_preserves_correlated_stages_and_raw_action(tmp_path):
    path = tmp_path / "run.jsonl"
    journal = DurableRunJournal(path, "run-1")
    sink = DurableTraceSink(MemoryExecutionTrace("trace-1"), journal)
    raw = '{"kind":"act","action":"browser.key","args":{"key":"f85e10"}}'

    sink.emit({
        "event_type": "AGENTLOOP_PROVENANCE", "stage": "model_input",
        "turn": 8, "iteration_id": "turn-8", "action_id": "action-8",
        "model_observation": {"elements": [{"ref": "f85e10", "role": "button"}]},
        "model_tool_contract": "browser.key: pinned contract",
        "feedback_returned_to_model": "",
    })
    sink.emit({
        "event_type": "AGENTLOOP_PROVENANCE", "stage": "raw_model_output",
        "turn": 8, "iteration_id": "turn-8", "action_id": "action-8",
        "raw_model_output": raw,
    })
    sink.emit({
        "event_type": "AGENTLOOP_PROVENANCE", "stage": "parsed_action",
        "turn": 8, "iteration_id": "turn-8", "action_id": "action-8",
        "parsed_action": "browser.key", "parsed_args": {"key": "f85e10"},
    })
    sink.emit({
        "event_type": "AGENTLOOP_PROVENANCE", "stage": "validation_result",
        "turn": 8, "iteration_id": "turn-8", "action_id": "action-8",
        "validation_result": "FAIL", "validation_input": {"key": "f85e10"},
        "failure_code": "invalid_browser_key",
        "feedback_returned_to_model": "Previous action failed: invalid_browser_key",
    })
    journal.close()

    state = read_durable_run_journal(path)
    stages = [event["stage"] for event in state["agentloop_provenance"]]
    assert stages == ["model_input", "raw_model_output", "parsed_action", "validation_result"]
    assert state["agentloop_provenance"][1]["raw_model_output"] == raw
    assert state["agentloop_provenance"][2]["parsed_action"] == "browser.key"
    assert state["agentloop_provenance"][3]["failure_code"] == "invalid_browser_key"
    assert all(event["iteration_id"] == "turn-8" for event in state["agentloop_provenance"])


def test_provenance_trace_redacts_secrets_without_redacting_keyboard_key():
    safe = redact_sensitive_text(
        'AGNES_API_KEY="secret-a" BRAVE_SEARCH_API_KEY=secret-b '
        'Authorization: Bearer secret-c '
        '{"key":"f85e10"}'
    )
    assert "secret-a" not in safe
    assert "secret-b" not in safe
    assert "secret-c" not in safe
    assert '"key":"f85e10"' in safe
