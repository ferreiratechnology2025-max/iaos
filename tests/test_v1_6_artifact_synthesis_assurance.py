"""Synthetic assurance for V1.6 artifact synthesis diagnostics.

These tests exercise only AgentLoop tracing/feedback composition.  They do not
call Agnes, Brave, Computer or a live entrypoint.
"""
import json

from application.agent_loop import ActionOutcome, AgentLoop, VerificationResult
from application.computer_runtime import ComputerElement, ComputerPageObservation
from application.execution_trace import MemoryExecutionTrace
from cognitive.agent_contracts import AgentGoal

from test_agent_loop import ScriptedProvider, act


GOAL = AgentGoal("artifact-assurance", "build the artifact", max_steps=8)
FINISH = '{"kind":"finish","claim":"done"}'


class ArtifactComputer:
    def __init__(self, aria_after_type="editor"):
        self.snapshot = 0
        self.aria_after_type = aria_after_type
        self.typed = []

    def observe(self, _step_id):
        self.snapshot += 1
        aria = "textbox ref=e1 name=Editor\n" + (self.aria_after_type if self.typed else "empty")
        return ComputerPageObservation(
            "https://artifact.example/", "Artifact", "artifact-session", self.snapshot,
            (ComputerElement("e1", "textbox", "Editor"),), aria,
        )

    def act(self, _step_id, decision, _observation):
        if decision.action == "browser.type":
            self.typed.append(decision.args["text"])
        return ActionOutcome(True, "executed")


def projection(observation):
    text = observation.aria_snapshot
    return {
        "contains_fact_a": "FACT_A" in text,
        "contains_fact_b": "FACT_B" in text,
        "preview_region_count": text.count("V1_6_PREVIEW"),
        "fresh_relative_to_last_mutation": "YES",
    }


def retired_test_mutation_provenance_and_post_mutation_observation_are_traceable():
    trace = MemoryExecutionTrace("artifact-provenance")
    computer = ArtifactComputer()
    result = AgentLoop(
        ScriptedProvider([act("browser.type", ref="e1", text="literal fact"), FINISH]),
        computer,
        lambda _observation: VerificationResult(True, {"ARTIFACT_CONTAINS_FACT_A": True}),
        trace_sink=trace,
        artifact_observation=projection,
    ).run(GOAL)

    attempts = [e for e in trace.events if e["event_type"] == "ARTIFACT_MUTATION_ATTEMPT"]
    results = [e for e in trace.events if e["event_type"] == "ARTIFACT_MUTATION_RESULT"]
    observations = [e for e in trace.events if e["event_type"] == "ARTIFACT_OBSERVATION"]
    assert result.status == "VERIFIED"
    assert attempts[0]["target_ref"] == "e1"
    assert attempts[0]["target_semantic_role"] == "textbox"
    assert attempts[0]["source_value_used"] == "NO"
    assert attempts[0]["literal_length"] == len("literal fact")
    assert results[0]["success"] is True and results[0]["failure_code"] is None
    assert observations[0]["after_mutation_sequence"] == attempts[0]["mutation_sequence"]
    assert observations[0]["contains_fact_a"] is False
    assert observations[0]["fresh_relative_to_last_mutation"] == "YES"
    assert "literal fact" not in json.dumps(trace.events)


def test_source_value_provenance_is_identity_only():
    # Reuse the established synthetic V1.5 research fixture; no provider or
    # external runtime is involved.
    from test_v1_5_exact_value_preservation import FINISH as V15_FINISH, READY, run, tsv

    trace = MemoryExecutionTrace("source-value-provenance")
    result, _provider, _session, _port = run(READY + [tsv("e1", 1, "deep_fact"), V15_FINISH], trace_sink=trace)
    event = next(e for e in trace.events if e["event_type"] == "ARTIFACT_MUTATION_ATTEMPT")
    use = result.source_value_uses[0]
    assert event["source_value_used"] == "YES"
    assert event["source_id"] == use.source_id
    assert event["source_field"] == "deep_fact"
    assert event["resolved_value_digest"] == use.value_digest
    assert event["resolved_value_length"] == use.value_length
    assert use.value_digest in json.dumps(trace.events)


def test_structured_feedback_and_identical_finish_progress_are_traceable():
    trace = MemoryExecutionTrace("finish-progress")
    verifier = lambda _observation: VerificationResult(
        False,
        {"ARTIFACT_CONTAINS_FACT_A": False, "ARTIFACT_CONTAINS_FACT_B": False,
         "PREVIEW_REGION_UNIQUE": False, "FRESH_FINAL_OBSERVATION": False},
        missing=("ARTIFACT_CONTAINS_FACT_A", "ARTIFACT_CONTAINS_FACT_B",
                 "PREVIEW_REGION_UNIQUE", "FRESH_FINAL_OBSERVATION"),
    )
    provider = ScriptedProvider([FINISH] * 4)
    result = AgentLoop(provider, ArtifactComputer(), verifier, trace_sink=trace).run(GOAL)
    feedback = [e for e in trace.events if e["event_type"] == "VERIFICATION_FEEDBACK"]
    progress = [e for e in trace.events if e["event_type"] == "FINISH_PROGRESS"]
    assert result.status == "TOO_MANY_FAILED_FINISHES_WITHOUT_PROGRESS"
    assert len(feedback) == len(progress) == 4
    assert all(e["failure_set"] == sorted(verifier(None).missing) for e in feedback)
    assert all(e["progress"] is False for e in progress)
    assert progress[-1]["previous_failure_set"] == progress[-1]["current_failure_set"]
    assert all("ARTIFACT_CONTAINS_FACT_A" in prompt for prompt in provider.prompts[1:])


def test_partial_progress_is_reported_without_changing_verifier_decision():
    trace = MemoryExecutionTrace("partial-progress")
    states = iter((
        VerificationResult(False, {"A": False, "B": False}, missing=("A", "B")),
        VerificationResult(False, {"A": True, "B": False}, satisfied=("A",), missing=("B",)),
        VerificationResult(False, {"A": True, "B": False}, satisfied=("A",), missing=("B",)),
        VerificationResult(False, {"A": True, "B": False}, satisfied=("A",), missing=("B",)),
        VerificationResult(False, {"A": True, "B": False}, satisfied=("A",), missing=("B",)),
        VerificationResult(False, {"A": True, "B": False}, satisfied=("A",), missing=("B",)),
    ))
    provider = ScriptedProvider([FINISH] * 6)
    result = AgentLoop( provider, ArtifactComputer(), lambda _observation: next(states), trace_sink=trace).run(GOAL)
    progress = [e for e in trace.events if e["event_type"] == "FINISH_PROGRESS"]
    assert result.status == "TOO_MANY_FAILED_FINISHES_WITHOUT_PROGRESS"
    assert progress[0]["progress"] is False
    assert progress[1]["progress"] is True
    assert progress[1]["resolved_failures"] == ["A"]

