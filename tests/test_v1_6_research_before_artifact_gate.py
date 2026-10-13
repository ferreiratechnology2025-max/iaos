import json
from dataclasses import dataclass

from application.agent_loop import ActionOutcome, AgentLoop, VerificationResult, is_artifact_mutation, prompt_header
from application.computer_runtime import ComputerElement, ComputerPageObservation
from application.execution_trace import MemoryExecutionTrace
from application.research_completion import ResearchCompletion
from application.source_facts import ResearchBudget
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse


def act(action, **args):
    return json.dumps({"kind": "act", "action": action, "args": args})


FINISH = '{"kind":"finish","claim":"done"}'


class Provider:
    provider_id = "synthetic"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return LLMResponse(self.replies.pop(0), "synthetic", "synthetic")


class Computer:
    def __init__(self):
        self.acted = []
        self.observed = 0

    def observe(self, _step):
        self.observed += 1
        return ComputerPageObservation(
            "https://websitewombat.app/build", "Build", "session", self.observed,
            (ComputerElement("e1", "textbox", "Title"),
             ComputerElement("e2", "button", "Close"),
             ComputerElement("e3", "button", "+ Page")),
            "- button \"Close\" [ref=e2]\n- button \"+ Page\" [ref=e3]",
        )

    def act(self, _step, decision, _observation):
        self.acted.append(decision)
        return ActionOutcome(True, "ok")


@dataclass(frozen=True)
class StaticPolicy:
    complete: bool
    valid_sources: int
    represented: frozenset[str]

    required = frozenset(("NASA", "ESA"))

    def evaluate(self, _budget, _facts, _deep_facts):
        return ResearchCompletion(self.complete, self.valid_sources, self.represented, self.required)

    def prompt_contract(self, _budget):
        return "Research completion is governed by the required publishers."


class TransitionPolicy(StaticPolicy):
    def __init__(self):
        super().__init__(False, 0, frozenset())
        self.evaluations = 0

    def evaluate(self, _budget, _facts, _deep_facts):
        self.evaluations += 1
        complete = self.evaluations >= 4
        return ResearchCompletion(complete, 2 if complete else 0,
                                  self.required if complete else frozenset(), self.required)


def run_gate(replies, policy):
    computer = Computer()
    trace = MemoryExecutionTrace("research-before-artifact")
    result = AgentLoop(
        Provider(replies), computer, lambda _obs: VerificationResult(True, {"ok": True}),
        enable_search=True,
        enable_read=True,
        research=ResearchBudget(target_valid_sources=2, max_search_attempts=4, max_deep_read_attempts=4),
        research_completion_policy=policy,
        require_research_complete_before_artifact=True,
        trace_sink=trace,
    ).run(AgentGoal("g", "research", max_steps=len(replies)))
    return result, computer, trace


def test_incomplete_research_blocks_mutation_before_computer_and_traces_reason():
    result, computer, trace = run_gate(
        [act("browser.type", ref="e1", text="artifact")] * 4,
        StaticPolicy(False, 0, frozenset()),
    )
    blocked = [e for e in trace.events if e["event_type"] == "ARTIFACT_MUTATION_BLOCKED"]
    assert result.status == "TOO_MANY_ACTION_FAILURES"
    assert computer.acted == []
    assert blocked and blocked[0]["failure_code"] == "research_incomplete_artifact_mutation_blocked"
    assert blocked[0]["valid_source_count"] == 0
    assert blocked[0]["publishers_satisfied"] == []
    assert trace.events[-1]["artifact_mutation_count"] == 0
    assert any(e["event_type"] == "OPERATIONAL_FEEDBACK" and
               e.get("failure_code") == "research_incomplete_artifact_mutation_blocked"
               for e in trace.events)


def test_two_valid_sources_without_required_publisher_diversity_stays_blocked():
    result, computer, trace = run_gate(
        [act("browser.type", ref="e1", text="artifact")] * 4,
        StaticPolicy(False, 2, frozenset(("NASA",))),
    )
    assert result.status == "TOO_MANY_ACTION_FAILURES"
    assert computer.acted == []
    blocked = next(e for e in trace.events if e["event_type"] == "ARTIFACT_MUTATION_BLOCKED")
    assert blocked["valid_source_count"] == 2
    assert blocked["publishers_satisfied"] == ["NASA"]


def test_required_publisher_completion_allows_mutation():
    result, computer, trace = run_gate(
        [act("browser.type", ref="e1", text="artifact"), FINISH],
        StaticPolicy(True, 2, frozenset(("NASA", "ESA"))),
    )
    assert result.status == "VERIFIED"
    assert len(computer.acted) == 1
    assert not any(e["event_type"] == "ARTIFACT_MUTATION_BLOCKED" for e in trace.events)


def test_non_artifact_computer_actions_remain_unaffected():
    assert not is_artifact_mutation("browser.navigate", None)
    assert not is_artifact_mutation("read_source", None)
    assert not is_artifact_mutation("browser.key", None)
    assert not is_artifact_mutation("browser.click", ComputerElement("e2", "button", "Close"))
    assert is_artifact_mutation("browser.click", ComputerElement("e3", "button", "+ Page"))

    result, computer, _trace = run_gate(
        [act("browser.click", ref="e2"), FINISH],
        StaticPolicy(False, 0, frozenset()),
    )
    assert result.status == "VERIFIED"
    assert len(computer.acted) == 1


def _research_prompt(policy, *, available):
    return prompt_header(
        search_enabled=True,
        read_enabled=True,
        research=ResearchBudget(target_valid_sources=2, max_search_attempts=4, max_deep_read_attempts=4),
        completion_policy=policy,
        artifact_mutations_available=available,
    )


def test_incomplete_research_hides_artifact_mutation_actions_but_keeps_research_actions():
    prompt = _research_prompt(StaticPolicy(False, 0, frozenset()), available=False)
    assert '"action":"browser.click"' not in prompt
    assert '"action":"browser.type"' not in prompt
    assert '"action":"browser.select_option"' not in prompt
    assert '"kind":"search"' in prompt
    assert '"kind":"read_source"' in prompt
    assert "ARTIFACT MUTATION ACTIONS are unavailable until research is complete." in prompt


def test_complete_research_exposes_artifact_mutation_actions_normally():
    prompt = _research_prompt(StaticPolicy(True, 2, frozenset(("NASA", "ESA"))), available=True)
    assert '"action":"browser.click"' in prompt
    assert '"action":"browser.type"' in prompt
    assert '"action":"browser.select_option"' in prompt
    assert "ARTIFACT MUTATION ACTIONS are unavailable" not in prompt


def test_action_exposure_can_transition_without_recreating_loop():
    policy = TransitionPolicy()
    provider = Provider([act("browser.click", ref="e2"), FINISH])
    result = AgentLoop(
        provider, Computer(), lambda _obs: VerificationResult(True, {"ok": True}),
        enable_search=True,
        enable_read=True,
        research=ResearchBudget(target_valid_sources=2, max_search_attempts=4, max_deep_read_attempts=4),
        research_completion_policy=policy,
        require_research_complete_before_artifact=True,
    ).run(AgentGoal("g", "research", max_steps=2))
    assert result.status == "VERIFIED"
    assert '"action":"browser.type"' not in provider.prompts[0]
    assert '"action":"browser.type"' in provider.prompts[1]


def test_direct_incomplete_research_bypass_remains_fail_closed():
    result, computer, trace = run_gate(
        [act("browser.type", ref="e1", text="artifact")],
        StaticPolicy(False, 0, frozenset()),
    )
    assert result.status == "TOO_MANY_ACTION_FAILURES" or result.status == "STEP_LIMIT"
    assert computer.acted == []
    assert any(e["event_type"] == "ARTIFACT_MUTATION_BLOCKED" for e in trace.events)


def test_non_research_prompt_action_exposure_is_unchanged():
    prompt = prompt_header()
    assert '"action":"browser.click"' in prompt
    assert '"action":"browser.type"' in prompt
    assert '"action":"browser.select_option"' in prompt
