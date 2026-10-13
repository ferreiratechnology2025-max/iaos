"""V1.2 micro-gate: sanitized action-failure feedback (covered element -> covered_by_dialog), no raw DOM, no ref prescription."""
import json
import re

import httpx
import pytest

from application.agent_loop import ActionOutcome, AgentLoop, VerificationResult, action_failure_note
from application.computer_runtime import (ComputerElement, ComputerPageObservation, ComputerRuntimeError,
                                          OpenBotComputerAdapter, classify_action_failure)
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

URL = "https://ai.8b.com/projects/x"
RAW_DOM = '<div role="dialog" class="fixed SECRET_DOM_MARKER"> subtree intercepts pointer events'
PLAYWRIGHT_BODY = ("click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for element to be visible, enabled and stable\n"
                   f"  - {RAW_DOM}\n  - retrying click action")


def adapter_with(status, body):
    def handler(request):
        return httpx.Response(status, json=body)
    a = OpenBotComputerAdapter("http://x", "bot", "tok", transport=httpx.MockTransport(handler))
    a._session_id, a._page_url = "s", URL
    return a


def click_error(status, body):
    with pytest.raises(ComputerRuntimeError) as info:
        adapter_with(status, body).click(URL, "s", 5, "e1")
    return str(info.value)


def test_covered_element_is_classified_and_raw_dom_is_not_exposed():
    msg = click_error(502, {"error": PLAYWRIGHT_BODY})
    assert msg == "computer_click_rejected:502:covered_by_dialog"
    assert "SECRET_DOM_MARKER" not in msg and "<" not in msg


@pytest.mark.parametrize("status,body,code", [
    (409, {"error": "That list of elements is out of date", "stale": True}, "stale_ref"),
    (502, {"error": "click: element is not enabled"}, "target_not_actionable"),
    (502, {"error": "click: Timeout 10000ms exceeded."}, "timeout"),
    (502, {"error": "The action failed."}, "unknown"),
    (502, {}, "unknown"),
])
def test_other_codes(status, body, code):
    assert click_error(status, body).endswith(":" + code)


def test_generic_502_is_safe_and_unclassified_text_never_leaks():
    msg = click_error(502, {"error": "weird <html>SECRET_DOM_MARKER</html>"})
    assert msg == "computer_click_rejected:502:unknown"


def test_classifier_returns_only_the_closed_vocabulary():
    f = classify_action_failure("click", 502, PLAYWRIGHT_BODY)
    assert (f.code, f.action) == ("covered_by_dialog", "click")


# ---------------- loop ----------------

GOAL = AgentGoal("g", "make a page", max_steps=60)
CLICK = json.dumps({"kind": "act", "action": "browser.click", "args": {"ref": "e1"}})
FINISH = '{"kind":"finish","claim":"done"}'


class Computer:
    def __init__(self, outcome):
        self.outcome, self.observed, self.acted, self.events = outcome, 0, [], []

    def observe(self, step_id):
        self.observed += 1
        self.events.append("observe")
        return ComputerPageObservation(URL, "t", "s", self.observed,
                                       (ComputerElement("e1", "button", "Try this design"),), "- x")

    def act(self, step_id, decision, observation):
        self.events.append("act")
        self.acted.append(decision.args.get("ref"))
        return self.outcome

    def navigate(self, step_id, url):  # pragma: no cover
        raise AssertionError


class Provider:
    provider_id = "scripted"

    def __init__(self, replies, computer):
        self.replies, self.prompts, self.computer = list(replies), [], computer

    def complete(self, prompt):
        self.prompts.append(prompt)
        self.computer.events.append("decide")
        return LLMResponse(self.replies.pop(0), "m", "scripted")


def run(code):
    computer = Computer(ActionOutcome(False, code))
    provider = Provider([CLICK, FINISH], computer)
    result = AgentLoop(provider, computer, lambda o: VerificationResult(True, {})).run(GOAL)
    return computer, provider, result


def test_covered_failure_reaches_cognition_with_fresh_observation_first():
    computer, provider, result = run("execution_failed:computer_click_rejected:502:covered_by_dialog")
    note = provider.prompts[1]
    assert "click was blocked because another dialog or overlay covers the target." in note
    assert "choose an actionable control from the foreground UI" in note
    assert "SECRET_DOM_MARKER" not in note and "502" not in note.split("PREVIOUS TURN RESULT:")[1].split("\n\n")[0]
    # a fresh observation happens between the failed act and the next decision; nothing was acted on automatically
    assert computer.events[:5] == ["observe", "decide", "act", "observe", "decide"]
    assert computer.acted == ["e1"]


def test_feedback_does_not_prescribe_a_ref_or_a_control():
    for reason in ("covered_by_dialog", "stale_ref", "target_not_actionable", "timeout", "unknown"):
        text = action_failure_note("browser.click", f"execution_failed:computer_click_rejected:502:{reason}")
        assert not re.search(r"\b[ef]?\d*e\d+\b", text), text
        assert "Try this design" not in text and "close" not in text.lower()


def test_generic_502_gets_safe_generic_failure():
    _, provider, _ = run("execution_failed:computer_click_rejected:502:unknown")
    assert "Previous action failed: click failed." in provider.prompts[1]


def test_non_classified_codes_keep_legacy_note():
    assert action_failure_note("browser.click", "denied_by_governance").startswith("Your previous action (browser.click) FAILED")
