import json

import httpx
import pytest

from application.computer_runtime import ComputerElement, ComputerPageObservation, ComputerRuntimeError, OpenBotComputerAdapter
from governance.aep_adapter import AEPAdapter, AdapterError
from governance.aep_executors import AEPExecutorRegistry, ComputerObservationExecutor, default_registry
from governance.aep_runtime import AEPRuntime
from governance.approval import ApprovalGate
from governance.auth_registry import create_authorization_registry
from governance.computer_observation import OBSERVE_RESOURCE, ComputerObservationAuthorizer
from governance.consumption import create_consumption_ledger
from governance.contract import Proposal, Provenance
from governance.evidence import InMemoryEvidenceStore
from governance.handle_binding import BindingTable


class FakeComputer:
    def __init__(self):
        self.calls = 0

    def observe(self):
        self.calls += 1
        return ComputerPageObservation(
            "https://todomvc.example/", "TodoMVC", "session-1", 3,
            (ComputerElement("e1", "checkbox", "Toggle", True, None),),
            "- listitem:\n  - checkbox [checked] [ref=e1]",
        )


def _run(proposal, runtime):
    evidence = InMemoryEvidenceStore()
    issuer, view = create_authorization_registry()
    _, consume = create_consumption_ledger()
    entries = {n: default_registry().lookup(n) for n in default_registry().actions()}
    entries["browser.observe"] = ComputerObservationExecutor(runtime)
    aep = AEPRuntime(AEPExecutorRegistry(entries), BindingTable(), view, consume)
    authorizer = ComputerObservationAuthorizer(evidence, issuer, ApprovalGate())
    action = authorizer.authorize(proposal)
    return AEPAdapter(aep).execute(action, evidence), evidence, authorizer


def _proposal(intent="obs-1", args=None, source="agent"):
    return Proposal(intent, "browser.observe", args or {}, Provenance(source, intent))


def test_observe_crosses_governance_and_returns_data_only_observation():
    computer = FakeComputer()
    result, evidence, _ = _run(_proposal(), computer)
    assert result.ok
    assert computer.calls == 1
    assert result.result_authority == "DATA_ONLY"
    assert result.result_provenance == "COMPUTER_OBSERVATION"
    out = result.structured_output
    assert out["snapshot_id"] == 3 and out["browser_session_id"] == "session-1"
    assert out["elements"][0] == {"ref": "e1", "role": "checkbox", "name": "Toggle", "checked": True}
    assert "[checked]" in out["aria_snapshot"]
    assert [e["action"] for e in evidence.events() if e["kind"] == "AUTHORIZATION"] == ["browser.observe"]


@pytest.mark.parametrize("args", [{"url": "https://evil.example/"}, {"page_url": "https://x.example/"}, {"x": 1}])
def test_observe_refuses_any_args_before_runtime(args):
    computer = FakeComputer()
    with pytest.raises(ValueError):
        _run(_proposal(args=args), computer)
    assert computer.calls == 0


def test_observe_refuses_non_authoritative_provenance_and_other_actions():
    computer = FakeComputer()
    with pytest.raises(ValueError):
        _run(_proposal(source="tool"), computer)
    authorizer = ComputerObservationAuthorizer(InMemoryEvidenceStore(), create_authorization_registry()[0], ApprovalGate())
    with pytest.raises(ValueError):
        authorizer.canonicalize(Proposal("x", "browser.click", {}, Provenance("agent", "x")))
    assert computer.calls == 0


def test_authorization_is_single_use():
    computer = FakeComputer()
    evidence = InMemoryEvidenceStore()
    issuer, view = create_authorization_registry()
    _, consume = create_consumption_ledger()
    entries = {n: default_registry().lookup(n) for n in default_registry().actions()}
    entries["browser.observe"] = ComputerObservationExecutor(computer)
    adapter = AEPAdapter(AEPRuntime(AEPExecutorRegistry(entries), BindingTable(), view, consume))
    action = ComputerObservationAuthorizer(evidence, issuer, ApprovalGate()).authorize(_proposal())
    assert adapter.execute(action, evidence).ok
    with pytest.raises(AdapterError):
        adapter.execute(action, evidence)
    assert computer.calls == 1


def _adapter(payload):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path == "/snapshot":
            return httpx.Response(200, json=payload)
        if request.url.path == "/run":
            return httpx.Response(200, json={"run": "session-1"})
        return httpx.Response(404)

    return OpenBotComputerAdapter("http://computer", "bot", "token", transport=httpx.MockTransport(handler)), calls


def test_adapter_observe_requests_aria_once_and_preserves_checked_and_value():
    payload = {
        "snapshotId": 5, "url": "https://todomvc.example/", "title": "TodoMVC",
        "elements": [
            {"ref": "e1", "role": "checkbox", "name": "Toggle", "checked": True},
            {"ref": "e2", "role": "textbox", "name": "New", "value": "abc"},
            {"ref": "e3", "role": "button", "name": "Go"},
        ],
        "ariaSnapshot": "- textbox [ref=e2]: abc",
    }
    adapter, calls = _adapter(payload)
    obs = adapter.observe()
    assert [(m, p) for m, p, _ in calls] == [("POST", "/snapshot"), ("GET", "/run")]
    assert calls[0][2] == {"includeAria": True, "requestHelp": False}  # never opens a handoff as a side effect
    assert obs.snapshot_id == 5 and obs.browser_session_id == "session-1"
    assert obs.elements[0].checked is True and obs.elements[0].value is None
    assert obs.elements[1].value == "abc" and obs.elements[1].checked is None
    assert obs.elements[2].checked is None
    assert obs.aria_snapshot.startswith("- textbox")


def test_adapter_observe_requires_aria_string():
    adapter, _ = _adapter({"snapshotId": 5, "url": "https://t.example/", "title": "T", "elements": []})
    with pytest.raises(ComputerRuntimeError):
        adapter.observe()


def test_adapter_observe_binds_following_actions_to_the_observed_snapshot():
    payload = {"snapshotId": 5, "url": "https://t.example/", "title": "T", "elements": [], "ariaSnapshot": ""}
    adapter, _ = _adapter(payload)
    obs = adapter.observe()
    with pytest.raises(ComputerRuntimeError):
        adapter.click("https://t.example/", "other-session", obs.snapshot_id, "e1")


def test_executor_and_authorizer_agree_on_the_resource():
    assert ComputerObservationExecutor.RESOURCE == OBSERVE_RESOURCE

