import json

import pytest

from cognitive.agent_contracts import ActDecision, AgentGoal, DecisionParseError, FinishDecision, RequestHumanDecision, WaitDecision, parse_decision

REFS = {"e1", "e2"}


def act(**kw):
    return json.dumps({"kind": "act", **kw})


def test_valid_decisions():
    assert parse_decision(act(action="browser.type", args={"ref": "e1", "text": "comprar café"}), REFS) == \
        ActDecision("browser.type", {"ref": "e1", "text": "comprar café"})
    assert parse_decision(act(action="browser.click", args={"ref": "e2"}), REFS) == ActDecision("browser.click", {"ref": "e2"})
    assert parse_decision(act(action="browser.key", args={"key": "Enter"}), REFS) == ActDecision("browser.key", {"key": "Enter"})
    assert parse_decision(act(action="browser.key", args={"key": "Enter", "ref": "e1"}), REFS).args["ref"] == "e1"
    assert parse_decision('{"kind":"finish","claim":"done"}') == FinishDecision("done")
    assert parse_decision('  {"kind":"finish","claim":"done"}\n') == FinishDecision("done")


@pytest.mark.parametrize("text", [
    "", "   ", "not json", "```json\n{\"kind\":\"finish\",\"claim\":\"x\"}\n```",
    "Sure! {\"kind\":\"finish\",\"claim\":\"x\"}", "[]", "\"x\"", "null",
    '{"kind":"finish"}', '{"kind":"finish","claim":""}', '{"kind":"finish","claim":1}',
    '{"kind":"finish","claim":"x","extra":1}', '{"kind":"wait","seconds":5}', '{"kind":"wait","reason":"x"}', '{"claim":"x"}',
    '{"kind":"finish","claim":"a","claim":"b"}',
    act(action="browser.navigate_and_read", args={"requested_url": "https://x.example/"}),
    act(action="browser.observe", args={}),
    act(action="browser.click", args={"ref": "e1", "page_url": "https://x.example/"}),
    act(action="browser.click", args={"ref": "e1", "snapshot_id": 3}),
    act(action="browser.click", args={}),
    act(action="browser.click", args={"ref": 1}),
    act(action="browser.click", args={"ref": "e9"}),
    act(action="browser.type", args={"ref": "e1"}),
    act(action="browser.type", args={"ref": "e1", "text": 5}),
    act(action="browser.key", args={"key": ""}),
    act(action="browser.key", args={"ref": "e1"}),
    act(action="browser.click", args=[]),
    json.dumps({"kind": "act", "action": "browser.click", "args": {"ref": "e1"}, "why": "x"}),
])
def test_invalid_decisions_fail_without_repair(text):
    with pytest.raises(DecisionParseError):
        parse_decision(text, REFS)


def test_non_string_input_fails():
    with pytest.raises(DecisionParseError):
        parse_decision(None, REFS)


def test_goal_validation():
    assert AgentGoal("g", "do it").max_steps == 20
    for kwargs in ({"goal_id": ""}, {"objective": " "}, {"max_steps": 0}, {"max_steps": True}):
        base = {"goal_id": "g", "objective": "o", **kwargs}
        with pytest.raises(ValueError):
            AgentGoal(**base)


def test_request_human_is_closed():
    assert parse_decision('{"kind":"request_human","reason_code":"authentication_required"}') == RequestHumanDecision("authentication_required")
    assert parse_decision('{"kind":"request_human","reason_code":"challenge_required"}') == RequestHumanDecision("challenge_required")
    for bad in (
        '{"kind":"request_human"}',
        '{"kind":"request_human","reason_code":"because I want to"}',
        '{"kind":"request_human","reason_code":"challenge_required","reason":"free text"}',
        '{"kind":"request_human","reason_code":"challenge_required","tool_call_id":"x"}',
        '{"kind":"request_human","reason_code":1}',
    ):
        with pytest.raises(DecisionParseError):
            parse_decision(bad)


def test_wait_is_closed_and_carries_no_duration():
    assert parse_decision('{"kind":"wait"}') == WaitDecision()
