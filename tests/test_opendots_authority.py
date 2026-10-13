import json

import httpx
import pytest

from governance.aep_adapter import AEPAdapter
from governance.aep_executors import AEPExecutorRegistry, opendots_executors
from governance.aep_runtime import AEPRuntime
from governance.approval import ApprovalGate
from governance.auth_registry import create_authorization_registry
from governance.consumption import create_consumption_ledger
from governance.contract import Proposal, Provenance
from governance.evidence import InMemoryEvidenceStore
from governance.handle_binding import BindingTable
from governance.opendots import (
    ArtifactReceipt,
    OpenDotsPageCreateAuthorizer,
    OpenDotsPageReadAuthorizer,
    read_proposal_from_receipt,
)


def _env(handler):
    transport = httpx.MockTransport(handler)
    entries = opendots_executors("https://opendots.test", "runtime-secret", transport)
    issuer, view = create_authorization_registry()
    _, consume = create_consumption_ledger()
    runtime = AEPRuntime(AEPExecutorRegistry(entries), BindingTable(), view, consume)
    return AEPAdapter(runtime), InMemoryEvidenceStore(), ApprovalGate(), issuer


def _execute(adapter, evidence, approval, authorizer, proposal):
    canon = authorizer.canonicalize(proposal)
    approval.grant("test", proposal.intent_id, canon.digest())
    return adapter.execute(authorizer.authorize(proposal), evidence)


def _create_proposal(**overrides):
    values = {"space_id": "space-a", "title": "Research", "content": "Artemis I\nRosetta"}
    values.update(overrides)
    return Proposal("create-1", "opendots.page.create", values, Provenance("agent", "create-1"))


def _read_proposal(receipt, **overrides):
    values = {"space_id": receipt.space_id, "page_id": receipt.page_id, "expected_revision": receipt.revision}
    values.update(overrides)
    return Proposal("read-1", "opendots.page.read", values, Provenance("governance", "read-1"))


def test_create_and_read_are_exact_governed_effects():
    def handler(request):
        assert request.headers["authorization"] == "Bearer runtime-secret"
        if request.method == "POST":
            assert request.url.path == "/api/spaces/space-a/pages"
            assert request.read() == b'{"title":"Research","content":"Artemis I\\nRosetta"}'
            return httpx.Response(201, json={"id": "page-a", "spaceId": "space-a", "title": "Research", "content": "Artemis I\nRosetta", "revision": 1})
        assert request.method == "GET"
        assert request.url.path == "/api/spaces/space-a/pages/page-a"
        return httpx.Response(200, json={"id": "page-a", "spaceId": "space-a", "title": "Research", "content": "Artemis I\nRosetta", "revision": 1})

    adapter, evidence, approval, issuer = _env(handler)
    create = OpenDotsPageCreateAuthorizer(evidence, issuer, approval)
    created = _execute(adapter, evidence, approval, create, _create_proposal())
    assert created.ok and created.structured_output["page_id"] == "page-a"
    receipt = ArtifactReceipt(**created.structured_output)
    read = OpenDotsPageReadAuthorizer(evidence, issuer, approval)
    observed = _execute(adapter, evidence, approval, read, _read_proposal(receipt))
    assert observed.ok and observed.structured_output["revision"] == 1


@pytest.mark.parametrize("field,value", [("space_id", "space-b"), ("title", "Other"), ("content", "Other")])
def test_create_receipt_mismatch_fails_closed(field, value):
    def handler(request):
        return httpx.Response(201, json={"id": "page-a", "spaceId": "space-a", "title": "Research", "content": "Artemis I", "revision": 1})
    adapter, evidence, approval, issuer = _env(handler)
    authorizer = OpenDotsPageCreateAuthorizer(evidence, issuer, approval)
    proposal = _create_proposal(**{field: value})
    result = _execute(adapter, evidence, approval, authorizer, proposal)
    assert not result.ok


def test_read_requires_exact_receipt_identity_and_revision():
    def handler(request):
        return httpx.Response(200, json={"id": "page-a", "spaceId": "space-a", "title": "Research", "content": "x", "revision": 2})
    adapter, evidence, approval, issuer = _env(handler)
    authorizer = OpenDotsPageReadAuthorizer(evidence, issuer, approval)
    receipt = ArtifactReceipt("space-a", "page-a", 1, "Research", "x")
    result = _execute(adapter, evidence, approval, authorizer, _read_proposal(receipt))
    assert not result.ok
    wrong_page = _execute(adapter, evidence, approval, authorizer, _read_proposal(receipt, page_id="page-b"))
    assert not wrong_page.ok


def test_follow_up_builder_cannot_use_model_page_id():
    receipt = ArtifactReceipt("space-a", "page-a", 1, "Research", "x")
    proposal = read_proposal_from_receipt(receipt, "read-1")
    assert proposal.args == {"space_id": "space-a", "page_id": "page-a", "expected_revision": 1}


@pytest.mark.parametrize("proposal", [
    Proposal("x", "opendots.page.create", {"space_id": "s", "title": "t", "content": "c", "url": "https://evil"}, Provenance("agent", "x")),
    Proposal("x", "opendots.page.create", {"space_id": "s", "title": "t", "content": "c", "method": "DELETE"}, Provenance("agent", "x")),
    Proposal("x", "opendots.page.create", {"space_id": "s", "title": "t", "content": "c", "token": "secret"}, Provenance("agent", "x")),
])
def test_arbitrary_transport_fields_are_rejected(proposal):
    evidence = InMemoryEvidenceStore()
    issuer, _ = create_authorization_registry()
    with pytest.raises(ValueError):
        OpenDotsPageCreateAuthorizer(evidence, issuer, ApprovalGate()).canonicalize(proposal)
