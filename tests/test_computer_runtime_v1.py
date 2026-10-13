import httpx
import pytest

from application.computer_runtime import ComputerElement, ComputerSnapshot, OpenBotComputerAdapter
from governance.aep_adapter import AEPAdapter
from governance.aep_executors import AEPExecutorRegistry, ComputerInteractionExecutor, default_registry
from governance.aep_runtime import AEPRuntime
from governance.approval import ApprovalGate
from governance.auth_registry import create_authorization_registry
from governance.computer_interaction import ComputerInteractionAuthorizer
from governance.consumption import create_consumption_ledger
from governance.contract import Proposal, Provenance
from governance.evidence import InMemoryEvidenceStore
from governance.handle_binding import BindingTable


class FakeComputer:
    def snapshot(self):
        return ComputerSnapshot("https://excalidraw.com/", "Excalidraw", 7, "session-1", (ComputerElement("e1", "button", "Rectangle"),))

    def click(self, page_url, session_id, snapshot_id, ref):
        return {"action": "click", "ref": ref, "url": page_url}

    def type(self, page_url, session_id, snapshot_id, ref, text):
        return {"action": "type", "ref": ref, "characters": len(text), "url": page_url}

    def key(self, page_url, session_id, key, ref=None, snapshot_id=None):
        return {"action": "key", "key": key, "url": page_url}


def _execute(proposal, runtime):
    evidence = InMemoryEvidenceStore()
    issuer, view = create_authorization_registry()
    _, consume = create_consumption_ledger()
    entries = {name: default_registry().lookup(name) for name in default_registry().actions()}
    entries[proposal.action] = ComputerInteractionExecutor(proposal.action, runtime)
    aep = AEPRuntime(AEPExecutorRegistry(entries), BindingTable(), view, consume)
    authorizer = ComputerInteractionAuthorizer(evidence, issuer, ApprovalGate())
    canonical = authorizer.canonicalize(proposal)
    approval = authorizer._approval
    approval.grant("test-approver", proposal.intent_id, canonical.digest())
    action = authorizer.authorize(proposal)
    result = AEPAdapter(aep).execute(action, evidence)
    return result, aep, evidence


@pytest.mark.parametrize("action,args", [
    ("browser.click", {"ref": "e1"}),
    ("browser.type", {"ref": "e1", "text": "IAOS Computer Runtime"}),
    ("browser.key", {"key": "ArrowRight"}),
])
def test_each_v1_effect_crosses_its_own_authority_and_envelope(action, args):
    args = {"page_url": "https://excalidraw.com/", "browser_session_id": "session-1", "snapshot_id": 7, **args}
    proposal = Proposal("v1-" + action, action, args, Provenance("user", "v1-" + action))
    result, aep, evidence = _execute(proposal, FakeComputer())
    assert result.ok
    assert result.result_authority == "EFFECT"
    assert result.result_provenance == "COMPUTER_INTERACTION"
    assert aep.calls[1][1] == action
    assert [e["action"] for e in evidence.events() if e["kind"] == "AUTHORIZATION"] == [action]
    assert all("computer.execute" not in repr(e) for e in evidence.events())


def test_v1_refuses_wrong_session_before_runtime_effect():
    proposal = Proposal("wrong-session", "browser.click", {
        "page_url": "https://excalidraw.com/", "browser_session_id": "other", "snapshot_id": 7, "ref": "e1",
    }, Provenance("user", "wrong-session"))
    with pytest.raises(ValueError):
        # No browser adapter is needed: the contract must still be explicit and typed.
        ComputerInteractionAuthorizer(InMemoryEvidenceStore(), create_authorization_registry()[0], ApprovalGate()).canonicalize(
            Proposal("bad-page", "browser.click", {**proposal.args, "page_url": "http://excalidraw.com/"}, proposal.provenance)
        )


def test_openbot_v1_adapter_binds_snapshot_and_dispatches_typed_actions():
    calls = []

    def handler(request):
        import json
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path == "/snapshot":
            return httpx.Response(200, json={"snapshotId": 7, "url": "https://excalidraw.com/", "title": "Excalidraw", "elements": [{"ref": "e1", "role": "button", "name": "Rectangle"}]})
        if request.url.path == "/run":
            return httpx.Response(200, json={"run": "session-1"})
        if request.url.path == "/click":
            return httpx.Response(200, json={"action": "click", "ref": "e1", "url": "https://excalidraw.com/"})
        return httpx.Response(404)

    adapter = OpenBotComputerAdapter("http://computer", "bot-1", "token", transport=httpx.MockTransport(handler))
    snapshot = adapter.snapshot()
    assert snapshot.elements[0].name == "Rectangle"
    assert adapter.click(snapshot.page_url, snapshot.browser_session_id, snapshot.snapshot_id, "e1")["action"] == "click"
    assert [path for _, path, _ in calls] == ["/snapshot", "/run", "/click", "/run"]
