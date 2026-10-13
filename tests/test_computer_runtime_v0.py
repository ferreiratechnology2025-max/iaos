import httpx
import pytest

from application.computer_runtime import ComputerRuntimeError, OpenBotComputerAdapter
from governance.aep_adapter import AEPAdapter
from governance.aep_executors import AEPExecutorRegistry, ComputerNavigateAndReadExecutor, default_registry
from governance.aep_runtime import AEPRuntime
from governance.approval import ApprovalGate
from governance.auth_registry import create_authorization_registry
from governance.computer_read import COMPUTER_READ_ACTION, ComputerReadAuthorizer
from governance.consumption import create_consumption_ledger
from governance.contract import Proposal, Provenance
from governance.evidence import InMemoryEvidenceStore
from governance.handle_binding import BindingTable


class FakeComputer:
    def navigate_and_read(self, requested_url):
        from application.computer_runtime import ComputerObservation

        return ComputerObservation(
            requested_url,
            requested_url,
            "Example Domain",
            "visible page text",
            "session-1",
            "2026-10-07T12:00:00+00:00",
        )


def _system(runtime):
    evidence = InMemoryEvidenceStore()
    issuer, view = create_authorization_registry()
    ledger, consume = create_consumption_ledger()
    entries = {name: default_registry().lookup(name) for name in default_registry().actions()}
    entries[COMPUTER_READ_ACTION] = ComputerNavigateAndReadExecutor(runtime)
    aep = AEPRuntime(AEPExecutorRegistry(entries), BindingTable(), view, consume)
    return evidence, issuer, aep


def test_v0_crosses_governance_and_returns_observation_without_generic_execute():
    evidence, issuer, aep = _system(FakeComputer())
    approval = ApprovalGate()
    authorizer = ComputerReadAuthorizer(evidence, issuer, approval)
    proposal = Proposal(
        "computer-v0-1",
        COMPUTER_READ_ACTION,
        {"requested_url": "https://example.com"},
        Provenance("user", "computer-v0-1"),
    )
    canonical = authorizer.canonicalize(proposal)
    approval.grant("test-approver", proposal.intent_id, canonical.digest())
    action = authorizer.authorize(proposal)
    result = AEPAdapter(aep).execute(action, evidence)

    assert result.ok
    assert result.structured_output == {
        "requested_url": "https://example.com",
        "final_url": "https://example.com",
        "page_title": "Example Domain",
        "visible_text": "visible page text",
        "browser_session_id": "session-1",
        "observed_at": "2026-10-07T12:00:00+00:00",
    }
    assert result.result_authority == "DATA_ONLY"
    assert result.result_provenance == "COMPUTER_OBSERVATION"
    assert aep.calls[1][1] == COMPUTER_READ_ACTION
    assert [event["action"] for event in evidence.events() if event["kind"] == "AUTHORIZATION"] == [COMPUTER_READ_ACTION]
    assert all("computer.execute" not in repr(event) for event in evidence.events())


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost/", "https://127.0.0.1/"])
def test_v0_requires_https_public_network(url):
    with pytest.raises(ValueError):
        ComputerReadAuthorizer(
            InMemoryEvidenceStore(), *create_authorization_registry()[:1], ApprovalGate()
        ).canonicalize(
            Proposal("bad", COMPUTER_READ_ACTION, {"requested_url": url}, Provenance("user", "bad"))
        )


def test_openbot_adapter_reads_once_and_rejects_cross_origin_redirect():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/snapshot":
            return httpx.Response(200, json={"url": "about:blank"})
        if request.url.path == "/navigate":
            return httpx.Response(200, json={"url": "https://evil.example/", "title": "x", "text": "y"})
        if request.url.path == "/run":
            return httpx.Response(200, json={"run": "session-1"})
        return httpx.Response(404)

    adapter = OpenBotComputerAdapter(
        "http://computer", "bot-1", "token", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ComputerRuntimeError, match="cross_origin_redirect"):
        adapter.navigate_and_read("https://example.com")
    assert [path for _, path in calls] == ["/snapshot", "/navigate", "/run"]
