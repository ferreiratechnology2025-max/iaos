"""FORBIDDEN_EFFECT_GUARD_GATE: Publish/Upgrade must not be reachable by click, type, Enter/Space or stale focus."""
import json

import pytest

from application.agent_loop import ActionOutcome, AgentLoop, HumanRequestOutcome, VerificationResult
from application.computer_runtime import ComputerControlState, ComputerElement, ComputerPageObservation
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

GOAL = AgentGoal("g", "make a page", max_steps=20)
FORBIDDEN = ("Publish", "Publicar", "Upgrade")
FINISH = '{"kind":"finish","claim":"done"}'
OK = lambda o: VerificationResult(True, {})


def act(action, **args):
    return json.dumps({"kind": "act", "action": action, "args": args})


def click(ref):
    return act("browser.click", ref=ref)


def key(k, ref=None):
    return act("browser.key", key=k, **({"ref": ref} if ref else {}))


class Computer:
    """e1=Send e2=Publish e3=Upgrade e6=textbox; ``e1_names`` renames e1 per observation (re-rendered refs)."""

    def __init__(self, e1_names=None):
        self.n, self.acted, self.e1_names = 0, [], e1_names or {}

    def observe(self, step_id):
        self.n += 1
        e1 = self.e1_names.get(self.n, "Send")
        els = (ComputerElement("e1", "button", e1), ComputerElement("e2", "button", "Publish"),
               ComputerElement("e3", "button", "Upgrade"), ComputerElement("e6", "textbox", "Prompt"))
        return ComputerPageObservation("https://ai.8b.com/", "8b", "s", self.n, els, "- x")

    def act(self, step_id, decision, observation):
        self.acted.append((decision.action.split(".")[1], dict(decision.args)))
        return ActionOutcome(True, "executed")

    def request_human(self, step_id, reason_code, tool_call_id):
        return HumanRequestOutcome(True, "req-1", "requested")

    def read_control_state(self, request_id=None):
        return ComputerControlState("bot", False, "req-1", "completed", False)


class Provider:
    provider_id = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, prompt):
        return LLMResponse(self.replies.pop(0), "m", "scripted")


def run(replies, computer=None):
    computer = computer or Computer()
    result = AgentLoop(Provider(replies), computer, OK, control=computer, sleep=lambda s: None,
                       forbidden_controls=FORBIDDEN).run(GOAL)
    return computer, result


@pytest.mark.parametrize("reply", [
    click("e2"), click("e3"),                              # click Publish / Upgrade
    act("browser.type", ref="e2", text="x"),               # type into a forbidden control
    key("Enter", "e2"), key("Space", "e3"), key("Control+Enter", "e2"),   # key on an explicit forbidden ref
])
def test_forbidden_effects_are_blocked_before_any_proposal(reply):
    computer, result = run([reply, FINISH])
    assert result.status == "GOAL_CONSTRAINT_VIOLATION" and computer.acted == []


def test_focus_that_becomes_a_forbidden_control_blocks_enter_without_ref():
    # click a harmless "Send"; the page re-renders and e1 is now "Publish"; a bare Enter would activate it
    computer, result = run([click("e1"), key("Enter"), FINISH], Computer({2: "Publish"}))
    assert result.status == "GOAL_CONSTRAINT_VIOLATION" and computer.acted == [("click", {"ref": "e1"})]


def test_tab_loses_known_focus_so_bare_enter_is_refused():
    computer, result = run([click("e1"), key("Tab"), key("Enter"), FINISH])
    assert result.status == "VERIFIED"
    assert [a[0] for a in computer.acted] == ["click", "key"] and computer.acted[1][1] == {"key": "Tab"}
    assert any("REFUSED key_focus_unknown" in line for line in result.log)


def test_bare_enter_with_unknown_initial_focus_is_refused():
    computer, result = run([key("Enter"), FINISH])
    assert computer.acted == [] and any("REFUSED" in line for line in result.log)


@pytest.mark.parametrize("first", [click("e6"), act("browser.type", ref="e6", text="hello")])
def test_enter_after_focusing_a_normal_control_is_allowed(first):
    computer, result = run([first, key("Enter"), FINISH])
    assert result.status == "VERIFIED" and [a[0] for a in computer.acted] == [first and json.loads(first)["action"].split(".")[1], "key"]
    assert computer.acted[1][1] == {"key": "Enter"}


def test_after_human_handback_focus_is_unknown_until_the_agent_refocuses():
    replies = [click("e6"), '{"kind":"request_human","reason_code":"authentication_required"}',
               key("Enter"), click("e6"), key("Enter"), FINISH]
    computer, result = run(replies)
    assert result.status == "VERIFIED"
    assert [a[0] for a in computer.acted] == ["click", "click", "key"]     # the first bare Enter never ran
    assert sum("REFUSED key_focus_unknown" in line for line in result.log) == 1
