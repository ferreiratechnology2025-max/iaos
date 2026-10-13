"""V1.2 micro-gate: independent decision/action failure budgets (3 tolerated, the 4th consecutive of a class ends the run)."""
import json

from application.agent_loop import ActionOutcome, AgentLoop, VerificationResult
from application.computer_runtime import ComputerElement, ComputerPageObservation
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

GOAL = AgentGoal("g", "make a page", max_steps=60)
OK_V = lambda o: VerificationResult(True, {})
COVERED = "execution_failed:computer_click_rejected:502:covered_by_dialog"
COVERED_NOTE = "click was blocked because another dialog or overlay covers the target"
FINISH = '{"kind":"finish","claim":"done"}'
JUNK = "not json"
NAV = '{"kind":"navigate","url":"https://ai.8b.com/"}'


def click(ref):
    return json.dumps({"kind": "act", "action": "browser.click", "args": {"ref": ref}})


class Computer:
    """act() outcomes are scripted in order; once exhausted every action succeeds."""

    def __init__(self, outcomes=()):
        self.outcomes, self.observed, self.acted, self.events = list(outcomes), 0, [], []

    def observe(self, step_id):
        self.observed += 1
        self.events.append("observe")
        return ComputerPageObservation("https://ai.8b.com/", "t", "s", self.observed,
                                       (ComputerElement("e1", "button", "Try this design"),), "- x")

    def act(self, step_id, decision, observation):
        self.events.append("act")
        self.acted.append(decision.args.get("ref"))
        return self.outcomes.pop(0) if self.outcomes else ActionOutcome(True, "executed")

    def navigate(self, step_id, url):
        self.events.append("navigate")
        return ActionOutcome(True, "navigated")


class Provider:
    provider_id = "scripted"

    def __init__(self, replies, computer):
        self.replies, self.prompts, self.computer = list(replies), [], computer

    def complete(self, prompt):
        self.prompts.append(prompt)
        self.computer.events.append("decide")
        return LLMResponse(self.replies.pop(0), "m", "scripted")


def run(replies, outcomes=(), goal=GOAL):
    computer = Computer(outcomes)
    provider = Provider(replies, computer)
    return computer, provider, AgentLoop(provider, computer, OK_V).run(goal)


def fail(code=COVERED):
    return ActionOutcome(False, code)


def test_live_run_sequence_continues_and_cognition_receives_the_feedback():
    # invalid_json -> successful navigate -> unknown_ref x2 -> valid click that is covered -> RUN CONTINUES
    computer, provider, result = run([JUNK, NAV, click("e99"), click("e99"), click("e1"), FINISH], [fail()])
    assert result.status == "VERIFIED"
    assert COVERED_NOTE in provider.prompts[5]            # the decision right after the covered click sees the note
    assert "SECRET" not in provider.prompts[5] and "502" not in provider.prompts[5].split("PREVIOUS TURN RESULT:")[1]
    # fresh observation sits between the failed action and the next cognition
    i = computer.events.index("act")
    assert computer.events[i + 1:i + 3] == ["observe", "decide"]


def test_three_action_failures_are_tolerated_and_the_third_is_seen_by_cognition():
    computer, provider, result = run([click("e1")] * 3 + [FINISH], [fail()] * 3)
    assert result.status == "VERIFIED"
    assert COVERED_NOTE in provider.prompts[3]


def test_fourth_consecutive_action_failure_ends_the_run():
    computer, _, result = run([click("e1")] * 4 + [FINISH], [fail()] * 4)
    assert result.status == "TOO_MANY_ACTION_FAILURES" and len(computer.acted) == 4


def test_fourth_consecutive_decision_failure_ends_the_run():
    _, provider, result = run([JUNK] * 4 + [FINISH])
    assert result.status == "TOO_MANY_DECISION_FAILURES" and len(provider.prompts) == 4


def test_unknown_ref_consumes_the_decision_budget():
    computer, _, result = run([click("e99")] * 4 + [FINISH])
    assert result.status == "TOO_MANY_DECISION_FAILURES" and computer.acted == []
    _, _, ok = run([click("e99")] * 3 + [FINISH])
    assert ok.status == "VERIFIED"


def test_valid_decision_resets_the_decision_counter_even_if_its_action_fails():
    _, _, result = run([JUNK] * 3 + [click("e1")] + [JUNK] * 3 + [FINISH], [fail()])
    assert result.status == "VERIFIED"


def test_successful_external_action_resets_the_action_counter():
    outcomes = [fail()] * 3 + [ActionOutcome(True, "executed")] + [fail()] * 3
    _, _, result = run([click("e1")] * 7 + [FINISH], outcomes)
    assert result.status == "VERIFIED"


def test_budgets_are_independent_and_not_summed():
    _, _, result = run([click("e1")] * 3 + [JUNK] * 3 + [FINISH], [fail()] * 3)
    assert result.status == "VERIFIED"


def test_max_steps_is_unchanged_by_the_budgets():
    goal = AgentGoal("g", "x", max_steps=5)
    computer, _, result = run([click("e1")] * 10, goal=goal)
    assert result.status == "STEP_LIMIT" and result.turns == 5
