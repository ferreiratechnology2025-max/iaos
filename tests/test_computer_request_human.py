import pytest

from application.agent_runtime import GovernedComputerSession
from application.computer_runtime import ComputerControlState, ComputerElement, ComputerPageObservation, ComputerRuntimeError, _parse_control_state
from governance.aep_executors import ComputerHumanRequestExecutor
from governance.computer_request_human import REASON_CODES, REQUEST_HUMAN_RESOURCE


class Port:
    def __init__(self):
        self.helps, self.controls = [], []

    def observe(self):
        return ComputerPageObservation("https://t.example/", "T", "s", 7, (ComputerElement("e1", "button", "Go"),), "- x", "cloudflare")

    def request_help(self, reason, tool_call_id):
        self.helps.append((reason, tool_call_id))
        return ComputerControlState("bot", True, "req-9", "waiting", False)

    def read_control_state(self, request_id=None):
        self.controls.append(request_id)
        return ComputerControlState("human", True, request_id, "taken", False)


def test_request_human_is_governed_and_calls_request_help_once():
    port = Port()
    session = GovernedComputerSession(port)
    outcome = session.request_human("s1-human", "authentication_required", "g-s1-human")
    assert outcome.ok and outcome.request_id == "req-9"
    assert port.helps == [(ComputerHumanRequestExecutor.REASONS["authentication_required"], "g-s1-human")]
    events = [e for e in session.evidence.events() if e["kind"] == "AUTHORIZATION"]
    assert [e["action"] for e in events] == ["browser.request_human"]


def test_unknown_reason_code_never_reaches_runtime():
    port = Port()
    session = GovernedComputerSession(port)
    outcome = session.request_human("s1-human", "free text please", "g-s1-human")
    assert not outcome.ok and port.helps == []


def test_reason_codes_and_resource_agree_between_authorizer_and_executor():
    assert set(ComputerHumanRequestExecutor.REASONS) == set(REASON_CODES)
    assert ComputerHumanRequestExecutor.RESOURCE == REQUEST_HUMAN_RESOURCE


def test_observation_keeps_challenge_and_read_control_is_not_governed():
    port = Port()
    session = GovernedComputerSession(port)
    assert session.observe("o1").challenge == "cloudflare"
    state = session.read_control_state("req-9")
    assert state.holder == "human" and port.controls == ["req-9"]
    assert [e["action"] for e in session.evidence.events() if e["kind"] == "AUTHORIZATION"] == ["browser.observe"]


def test_control_payload_parsing_is_strict():
    ok = _parse_control_state({"holder": "bot", "requested": False, "resumeSnapshotRequired": True,
                               "request": {"id": "r", "status": "completed"}})
    assert ok.request_status == "completed" and ok.resume_snapshot_required is True
    for bad in ({"holder": "robot", "requested": False, "resumeSnapshotRequired": False},
                {"holder": "bot", "requested": False},
                {"holder": "bot", "requested": False, "resumeSnapshotRequired": False, "request": {"id": 1}}):
        with pytest.raises(ComputerRuntimeError):
            _parse_control_state(bad)
