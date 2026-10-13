import json

import pytest

from application.agent_loop import AgentLoop, VerificationResult
from application.agent_runtime import build_agent_loop
from application.source_facts import SearchOutcome
from application.execution_trace import JsonlExecutionTrace, MemoryExecutionTrace
from cognitive.agent_contracts import AgentGoal

from test_agent_loop import FakeComputer, ScriptedProvider, OK, act
from test_v1_5_multi_source_micro_gate import CONFIG15, GOAL_TEXT, Port15, SA, SB, READ1, READ2, brave15


def retired_test_trace_reconstructs_decision_action_feedback_and_termination_without_raw_text():
    trace = MemoryExecutionTrace("trace-gate")
    provider = ScriptedProvider([act("browser.click", ref="e2"), '{"kind":"finish","claim":"done"}'])
    result = AgentLoop(provider, FakeComputer(), OK, trace_sink=trace).run(AgentGoal("g", "do the thing", max_steps=2))

    kinds = [event["event_type"] for event in trace.events]
    assert kinds[0] == "TRACE_CREATED"
    assert kinds.index("TRACE_CREATED") < kinds.index("COGNITION_DECISION")
    assert "BROWSER_ACTION" in kinds and "ACTION_RESULT" in kinds
    assert "FINISH_ATTEMPT" in kinds and "VERIFIER_RESULT" in kinds
    assert kinds[-2:] == ["TERMINATION", "FINAL_STATE"]
    assert result.status == "VERIFIED"
    encoded = json.dumps(trace.events, ensure_ascii=False)
    assert "do the thing" not in encoded
    assert "done" not in encoded


def test_trace_does_not_change_agent_result_or_provider_prompts():
    replies = [act("browser.click", ref="e2"), '{"kind":"finish","claim":"done"}']
    plain_provider = ScriptedProvider(replies.copy())
    traced_provider = ScriptedProvider(replies.copy())
    plain = AgentLoop(plain_provider, FakeComputer(), OK).run(AgentGoal("g", "o", max_steps=2))
    traced = AgentLoop(traced_provider, FakeComputer(), OK, trace_sink=MemoryExecutionTrace()).run(AgentGoal("g", "o", max_steps=2))
    assert (plain.status, plain.turns, plain.claim) == (traced.status, traced.turns, traced.claim)
    assert plain_provider.prompts == traced_provider.prompts


def test_trace_sink_must_be_available_before_run(tmp_path):
    with pytest.raises(OSError):
        JsonlExecutionTrace(tmp_path / "missing" / "events.jsonl", "run-1")


def test_jsonl_trace_flushes_each_event(tmp_path):
    path = tmp_path / "events.jsonl"
    trace = JsonlExecutionTrace(path, "run-1")
    trace.emit({"event_type": "TRACE_CREATED", "turn": 0})
    assert path.read_text(encoding="utf-8").count("TRACE_CREATED") == 1
    trace.close()
    event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert event["run_id"] == "run-1" and event["order"] == 1 and "timestamp" in event


def test_trace_sanitizes_sensitive_action_values():
    from application.execution_trace import sanitized_args
    data = sanitized_args({"ref": "e1", "text": "secret-value", "token": "never-store"})
    assert data["ref"] == "e1"
    assert data["text"]["length"] == len("secret-value")
    assert "secret-value" not in json.dumps(data)
    assert "never-store" not in json.dumps(data)


def test_trace_covers_search_read_admission_and_research_state():
    trace = MemoryExecutionTrace("research-trace")
    port = Port15({
        "https://docs.example.org/guide/alpha": "alpha fact",
        "https://docs.example.org/guide/beta": "beta fact",
    })
    provider = ScriptedProvider([SA, SB, READ1, READ2, '{"kind":"finish","claim":"done"}'])
    loop, _session = build_agent_loop(
        provider, port, lambda _observation: VerificationResult(True, {}),
        allowed_origins=("https://wombat.example",), search=CONFIG15,
        search_executor=brave15(), allow_read=True, max_deep_reads=2,
        sleep=lambda _: None, trace_sink=trace,
    )
    loop.run(AgentGoal("research", GOAL_TEXT, max_steps=14))
    kinds = {event["event_type"] for event in trace.events}
    assert {"RESEARCH_STATE", "SEARCH_ATTEMPT", "SEARCH_RESULT", "DEEP_READ_ATTEMPT",
            "DEEP_READ_RESULT", "FACT_ADMITTED", "FINISH_ATTEMPT", "VERIFIER_RESULT",
            "TERMINATION", "FINAL_STATE"} <= kinds


def test_trace_records_sanitized_search_domain_filter_diagnostic():
    class SearchFailureComputer(FakeComputer):
        def search(self, step_id, query):
            return SearchOutcome(False, "execution_failed:result_domain_out_of_scope",
                                 domain_filter={
                                     "expected_domains": ["esa.int", "nasa.gov"],
                                     "returned_hosts": ["www.nasa.gov", "evil.example"],
                                     "accepted_hosts": ["www.nasa.gov"],
                                     "rejected_hosts": ["evil.example"],
                                     "result_count": 2,
                                     "in_scope_count": 1,
                                 })

    trace = MemoryExecutionTrace("domain-filter-trace")
    result = AgentLoop(
        ScriptedProvider(['{"kind":"search","query":"mission launch"}']),
        SearchFailureComputer(), OK, trace_sink=trace, enable_search=True,
    ).run(AgentGoal("g", "research", max_steps=1))
    filters = [event for event in trace.events if event["event_type"] == "SEARCH_DOMAIN_FILTER"]
    assert result.status == "STEP_LIMIT"
    assert len(filters) == 1
    assert filters[0]["returned_hosts"] == ["www.nasa.gov", "evil.example"]
    assert filters[0]["in_scope_count"] == 1


def test_invalid_decision_and_unexpected_exception_are_traceable():
    trace = MemoryExecutionTrace("failure-trace")
    result = AgentLoop(ScriptedProvider(["not-json", '{"kind":"finish","claim":"done"}']),
                       FakeComputer(), OK, trace_sink=trace).run(AgentGoal("g", "o", max_steps=2))
    kinds = [event["event_type"] for event in trace.events]
    assert result.status == "VERIFIED" and "DECISION_FAILURE" in kinds and "OPERATIONAL_FEEDBACK" in kinds

    broken = MemoryExecutionTrace("exception-trace")
    from application.source_facts import ResearchBudget
    with pytest.raises(ValueError):
        AgentLoop(ScriptedProvider([]), FakeComputer(), OK, enable_search=True,
                  research=ResearchBudget(), max_admitted_sources=0, trace_sink=broken).run(
                      AgentGoal("g", "o", max_steps=1))
    assert [event["event_type"] for event in broken.events] == ["TRACE_CREATED", "TERMINATION", "FINAL_STATE"]
