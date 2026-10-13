import json

import pytest

from application.agent_loop import AgentLoop, VerificationResult
from application.research_completion import ResearchCompletion
from application.source_facts import ResearchBudget
from scripts.agent_opendots_v16_live import (
    DurableRunJournal,
    DurableTraceSink,
    OpenDotsHarnessPreflightError,
    read_durable_run_journal,
)
from application.execution_trace import MemoryExecutionTrace
from test_agent_loop import FakeComputer, ScriptedProvider
from cognitive.agent_contracts import AgentGoal


def journal(tmp_path):
    return DurableRunJournal(tmp_path / "run.jsonl", "run-1")


def test_jit_failure_is_pre_run_and_not_consumed(tmp_path):
    j = journal(tmp_path)
    j.append("RUN_STARTED")
    j.append("JIT_STARTED")
    j.append("JIT_FAILED", exception_type="ComputerRuntimeError")
    j.append("TERMINAL_STATUS", status="PRE_RUN_OPERATIONAL_FAILURE")
    j.close()
    state = read_durable_run_journal(tmp_path / "run.jsonl")
    assert state["jit_status"] == "FAILED"
    assert state["loop_invoked"] is False
    assert state["attempt_consumed"] == "NO"


@pytest.mark.parametrize("kind", ["model_turn", "search_effect"])
def test_boundary_then_crash_is_consumed(tmp_path, kind):
    j = journal(tmp_path)
    j.append("RUN_STARTED")
    j.append("JIT_PASSED")
    j.append("LOOP_INVOKED")
    j.append("CONSUMPTION_BOUNDARY", kind=kind, ordinal=1, turn=1)
    j.append("UNHANDLED_EXCEPTION", exception_type="RuntimeError", stage="live-run")
    j.close()
    assert read_durable_run_journal(tmp_path / "run.jsonl")["attempt_consumed"] == "YES"


def test_result_and_terminal_survive_stdout_failure(tmp_path):
    j = journal(tmp_path)
    j.append("RUN_RESULT_READY", completed=True, status="VERIFIED")
    j.append("TERMINAL_STATUS", status="PASS")
    j.close()
    state = read_durable_run_journal(tmp_path / "run.jsonl")
    assert state["terminal_status"] == "PASS"
    assert state["last_durable_event"] == "TERMINAL_STATUS"


def test_restore_diagnostic_survives_later_failure(tmp_path):
    j = journal(tmp_path)
    j.append(
        "RESTORE_DIAGNOSTIC",
        requested_return_url="https://nasa.gov/mission/artemis-i/",
        navigation_attempted=True,
        navigation_succeeded=False,
        navigation_final_url=None,
        observation_attempted=False,
        observation_succeeded=False,
        observed_url=None,
        nav_url_matches_return=False,
        observed_url_matches_return=False,
        failure_substage="NAVIGATION",
    )
    j.append("UNHANDLED_EXCEPTION", exception_type="RuntimeError", stage="live-run")
    j.close()
    assert read_durable_run_journal(tmp_path / "run.jsonl")["restore_diagnostics_count"] == 1


def test_journal_unavailable_blocks_before_run(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.agent_opendots_v16_live.os.fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OpenDotsHarnessPreflightError):
        DurableRunJournal(tmp_path / "run.jsonl", "run-1")


def test_loop_invoked_without_boundary_remains_unknown(tmp_path):
    j = journal(tmp_path)
    j.append("RUN_STARTED")
    j.append("JIT_PASSED")
    j.append("LOOP_INVOKED")
    j.close()
    assert read_durable_run_journal(tmp_path / "run.jsonl")["attempt_consumed"] == "UNKNOWN"


def test_truncated_final_record_recovers_prior_state(tmp_path):
    path = tmp_path / "run.jsonl"
    j = DurableRunJournal(path, "run-1")
    j.append("RUN_STARTED")
    j.append("JIT_PASSED")
    j.close()
    with path.open("ab") as handle:
        handle.write(b'{"run_id":"run-1","seq":3,"event":"LOOP_INVO')
    state = read_durable_run_journal(path)
    assert state["run_started"] is True
    assert state["jit_status"] == "PASSED"
    assert state["last_durable_event"] == "JIT_PASSED"


def test_intermediate_corruption_is_not_ignored(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text('{"event":"RUN_STARTED"}\nnot-json\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_durable_run_journal(path)


def test_journal_records_are_secret_free(tmp_path):
    j = journal(tmp_path)
    j.append("CONSUMPTION_BOUNDARY", kind="model_turn", ordinal=1, turn=1)
    j.close()
    text = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert "prompt" not in text.lower()
    assert "token" not in text.lower()
    assert "response" not in text.lower()


def test_model_boundary_is_durable_before_provider_returns(tmp_path):
    path = tmp_path / "run.jsonl"
    journal_obj = DurableRunJournal(path, "run-1")

    class Provider(ScriptedProvider):
        def complete(self, prompt):
            state = read_durable_run_journal(path)
            assert state["last_durable_event"] == "CONSUMPTION_BOUNDARY"
            return super().complete(prompt)

    AgentLoop(
        Provider(['{"kind":"finish","claim":"done"}']), FakeComputer(),
        lambda _observation: VerificationResult(True, {}),
        consumption_boundary_sink=lambda event: journal_obj.append(
            "CONSUMPTION_BOUNDARY", kind=event.kind, ordinal=event.ordinal, turn=event.turn
        ),
    ).run(AgentGoal("g", "o", max_steps=1))
    journal_obj.close()


def test_search_boundary_is_durable_before_search_effect(tmp_path):
    path = tmp_path / "run.jsonl"
    journal_obj = DurableRunJournal(path, "run-1")

    class Computer(FakeComputer):
        def search(self, _step_id, _query):
            assert read_durable_run_journal(path)["last_durable_event"] == "CONSUMPTION_BOUNDARY"
            from application.source_facts import SearchOutcome
            return SearchOutcome(False, "no_result")

    AgentLoop(
        ScriptedProvider(['{"kind":"search","query":"mission"}']), Computer(),
        lambda _observation: VerificationResult(True, {}), enable_search=True,
        consumption_boundary_sink=lambda event: journal_obj.append(
            "CONSUMPTION_BOUNDARY", kind=event.kind, ordinal=event.ordinal, turn=event.turn
        ),
    ).run(AgentGoal("g", "o", max_steps=1))
    journal_obj.close()


def test_action_failure_sequence_and_trigger_survive_fresh_journal_readback(tmp_path):
    j = journal(tmp_path)
    for index, (reason, before, after, trigger) in enumerate((
        ("covered_by_dialog", 0, 1, False),
        ("browser_page_changed", 1, 2, False),
        ("execution_failed", 2, 3, True),
    ), 1):
        j.append(
            "ACTION_FAILURE",
            action="browser.click",
            tool_kind="browser.click",
            result="FAIL",
            failure_code=reason,
            safe_exception_class=None,
            failure_counter_before=before,
            failure_counter_after=after,
            configured_failure_threshold=2,
            too_many_action_failures_triggered=trigger,
        )
    j.append("TERMINAL_STATUS", status="FAIL")
    j.close()

    fresh = read_durable_run_journal(tmp_path / "run.jsonl")
    assert fresh["action_failure_sequence"][0]["tool_kind"] == "browser.click"
    assert fresh["action_failure_sequence"][0]["failure_code"] == "covered_by_dialog"
    assert fresh["failed_action_counts_by_kind"] == {"browser.click": 3}
    assert fresh["failure_counter_transitions"][-1] == {
        "before": 2, "after": 3, "threshold": 2, "kind": "browser.click",
    }
    assert fresh["too_many_action_failures_trigger"]["failure_code"] == "execution_failed"
    raw = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert "token" not in raw.lower()
    assert "authorization" not in raw.lower()
    assert "prompt" not in raw.lower()


def test_durable_trace_sink_persists_only_safe_action_failure_metadata(tmp_path):
    j = journal(tmp_path)
    sink = DurableTraceSink(MemoryExecutionTrace("action-failure"), j)
    sink.emit({
        "event_type": "ACTION_RESULT",
        "action": "browser.click",
        "tool_kind": "browser.click",
        "result": "FAIL",
        "failure_code": "error_ComputerRuntimeError",
        "safe_exception_class": "ComputerRuntimeError",
        "failure_counter_before": 2,
        "failure_counter_after": 3,
        "configured_failure_threshold": 2,
        "too_many_action_failures_triggered": True,
        "prompt": "must not persist",
        "token": "must not persist",
    })
    j.close()
    fresh = read_durable_run_journal(tmp_path / "run.jsonl")
    assert fresh["action_failure_sequence"][0]["failure_code"] == "error_ComputerRuntimeError"
    raw = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert "must not persist" not in raw
    assert "prompt" not in raw.lower()
    assert "token" not in raw.lower()


@pytest.mark.parametrize("action,tool_kind,code", [
    ("browser.type_source_value", "browser.type_source_value", "target_not_editable"),
    ("browser.key", "browser.key", "no_focused_control"),
    ("browser.type", "browser.type", "artifact_target_unresolved"),
    ("browser.type", "browser.type", "research_incomplete_artifact_mutation_blocked"),
])
def test_formerly_non_durable_failure_sites_use_action_failure_record(tmp_path, action, tool_kind, code):
    j = journal(tmp_path)
    sink = DurableTraceSink(MemoryExecutionTrace("action-failure-sites"), j)
    sink.emit({
        "event_type": "ACTION_RESULT", "action": action, "tool_kind": tool_kind,
        "result": "FAIL", "failure_code": code, "safe_exception_class": None,
        "failure_counter_before": 0, "failure_counter_after": 1,
        "configured_failure_threshold": 3,
        "too_many_action_failures_triggered": False,
    })
    j.close()
    fresh = read_durable_run_journal(tmp_path / "run.jsonl")
    assert fresh["action_failure_sequence"][0]["tool_kind"] == tool_kind
    assert fresh["action_failure_sequence"][0]["failure_code"] == code


def test_counter_three_to_four_persists_terminal_trigger(tmp_path):
    j = journal(tmp_path)
    sink = DurableTraceSink(MemoryExecutionTrace("threshold"), j)
    for before, after in ((2, 3), (3, 4)):
        sink.emit({
            "event_type": "ACTION_RESULT", "action": "browser.type",
            "tool_kind": "browser.type", "result": "FAIL", "failure_code": "synthetic_failure",
            "safe_exception_class": None, "failure_counter_before": before,
            "failure_counter_after": after, "configured_failure_threshold": 3,
            "too_many_action_failures_triggered": after > 3,
        })
    j.append("TERMINAL_STATUS", status="TOO_MANY_ACTION_FAILURES")
    j.close()
    fresh = read_durable_run_journal(tmp_path / "run.jsonl")
    assert fresh["failure_counter_transitions"] == [
        {"before": 2, "after": 3, "threshold": 3, "kind": "browser.type"},
        {"before": 3, "after": 4, "threshold": 3, "kind": "browser.type"},
    ]
    assert fresh["too_many_action_failures_trigger"] == {
        "action": "browser.type", "tool_kind": "browser.type", "result": "FAIL",
        "failure_code": "synthetic_failure", "safe_exception_class": None,
        "failure_counter_before": 3, "failure_counter_after": 4,
        "configured_failure_threshold": 3,
        "too_many_action_failures_triggered": True,
    }


class _CompleteResearchPolicy:
    def evaluate(self, _budget, _facts, _deep_facts):
        return ResearchCompletion(True, 0, frozenset(), frozenset())

    def prompt_contract(self, _budget):
        return "research complete"


@pytest.mark.parametrize("reply,expected", [
    ("not json", "invalid_json"),
    ('{"kind":"research_handoff","claims":[{"claim_text":"x"}]}', "claim_proposal_invalid"),
    ('{"kind":"research_handoff","claims":[{"claim_text":"x","read_execution_id":"R1"}]}', "read_not_found"),
])
def test_claim_admission_failure_reason_survives_fresh_journal_readback(tmp_path, reply, expected):
    path = tmp_path / "run.jsonl"
    j = DurableRunJournal(path, "run-claims")
    sink = DurableTraceSink(MemoryExecutionTrace("claim-admission"), j)
    result = AgentLoop(
        ScriptedProvider([reply]), FakeComputer(), lambda _observation: VerificationResult(True, {}),
        enable_search=True, enable_read=True,
        research=ResearchBudget(target_valid_sources=1, max_search_attempts=1, max_deep_read_attempts=1),
        research_completion_policy=_CompleteResearchPolicy(),
        stop_after_research_gate=True, trace_sink=sink,
    ).run(AgentGoal("g", "research", max_steps=1))
    j.close()
    fresh = read_durable_run_journal(path)
    assert result.status == "RESEARCH_CLAIM_ADMISSION_FAILED"
    assert fresh["research_claim_admission_failures"] == [{
        "failure_code": expected,
        "failure_stage": "parse" if expected != "read_not_found" else "admission",
    }]
