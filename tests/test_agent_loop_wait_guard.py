"""V1.2 synthetic gate: WAIT is bounded and external-effect free; forbidden controls never reach Governance."""
import json

import pytest

from application.agent_loop import ActionOutcome, AgentLoop, VerificationResult, build_prompt
from application.computer_runtime import ComputerElement, ComputerPageObservation
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

GOAL = AgentGoal("g", "make a page", max_steps=60)
WAIT, FINISH = '{"kind":"wait"}', '{"kind":"finish","claim":"done"}'
OK = lambda o: VerificationResult(True, {})


def click(ref):
    return json.dumps({"kind": "act", "action": "browser.click", "args": {"ref": ref}})


class Computer:
    def __init__(self):
        self.observed, self.acted = 0, []

    def observe(self, step_id):
        self.observed += 1
        elements = (ComputerElement("e1", "button", "Send"), ComputerElement("e2", "button", "Publish"),
                    ComputerElement("e3", "button", "Upgrade to Pro"), ComputerElement("e4", "link", "Publicar agora"),
                    ComputerElement("e5", "button", "Republish history"))
        return ComputerPageObservation("https://ai.8b.com/", "8b", "s", self.observed, elements, "- x")

    def act(self, step_id, decision, observation):
        self.acted.append(decision.args.get("ref"))
        return ActionOutcome(True, "executed")

    def navigate(self, step_id, url):  # pragma: no cover - never expected here
        raise AssertionError("no navigation expected")


class Provider:
    provider_id = "scripted"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return LLMResponse(self.replies.pop(0), "m", "scripted")


def loop(replies, computer=None, sleeps=None, **kw):
    computer = computer or Computer()
    provider = Provider(replies)
    sleeps = sleeps if sleeps is not None else []
    return computer, provider, AgentLoop(provider, computer, OK, sleep=sleeps.append, **kw)


def test_1_wait_has_no_external_action_and_is_followed_by_a_fresh_observation():
    sleeps = []
    computer, provider, lp = loop([WAIT, WAIT, FINISH], sleeps=sleeps)
    result = lp.run(GOAL)
    assert result.status == "VERIFIED" and result.waits == 2
    assert computer.acted == []                       # no external action
    assert sleeps == [3.0, 3.0]                        # application-owned interval
    assert computer.observed == 4                      # one fresh observation per decision (+ final)
    assert "You waited" in provider.prompts[1]


def test_2_consecutive_waits_are_bounded():
    computer, provider, lp = loop([WAIT] * 30)
    result = lp.run(GOAL)
    assert result.status == "WAIT_LIMIT_EXCEEDED" and result.waits == 21 and computer.acted == []
    assert len(provider.prompts) == 21


def test_waits_reset_after_a_real_decision():
    replies = ([WAIT] * 20 + [click("e1")]) * 2 + [FINISH]
    computer, _, lp = loop(replies)
    assert lp.run(GOAL).status == "VERIFIED" and computer.acted == ["e1", "e1"]


@pytest.mark.parametrize("ref", ["e2", "e3", "e4"])
def test_3_and_4_forbidden_clicks_are_blocked_before_governance(ref):
    computer, provider, lp = loop([click(ref), FINISH], forbidden_controls=("Publish", "Publicar", "Upgrade"))
    result = lp.run(GOAL)
    assert result.status == "GOAL_CONSTRAINT_VIOLATION"
    assert computer.acted == [] and result.clicks == [] and len(provider.prompts) == 1


def test_allowed_click_passes_and_is_recorded_and_word_boundaries_hold():
    computer, _, lp = loop([click("e1"), click("e5"), FINISH], forbidden_controls=("Publish", "Publicar", "Upgrade"))
    result = lp.run(GOAL)
    assert result.status == "VERIFIED" and computer.acted == ["e1", "e5"]  # "Republish history" is not the Publish control
    assert result.clicks == ["button:Send", "button:Republish history"]


def test_guard_uses_the_trusted_observation_not_the_model_text():
    # the model cannot smuggle the ref past the guard by describing it differently
    computer, _, lp = loop(['{"kind":"act","action":"browser.click","args":{"ref":"e2"}}'], forbidden_controls=("publish",))
    assert lp.run(GOAL).status == "GOAL_CONSTRAINT_VIOLATION" and computer.acted == []


def test_prompt_offers_wait_without_a_duration():
    prompt = build_prompt(GOAL, Computer().observe("x"), 1, None)
    assert '{"kind":"wait"}' in prompt and "seconds" not in prompt.split("WAIT:")[1].split("NAVIGATION:")[0].lower().replace("shortly", "")
