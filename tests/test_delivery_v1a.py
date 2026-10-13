import json

import pytest

from application.daily_brief_history import DailyBriefRun, InMemoryDailyBriefHistoryStore
from application.delivery import (
    DeliveryAttempt,
    FakeEmailSendExecutor,
    InMemoryDeliveryStore,
    build_offline_delivery_service,
)
from application.google_connection import GoogleCredentialBroker, GoogleCredentialRecord, InMemoryGoogleCredentialStore
from governance.contract import Proposal, Provenance
from governance.external_read import EXTERNAL_READ_ACTIONS
from governance.external_write import EMAIL_SEND_ACTIONS, EmailSendDescriptor


def _broker(email="owner@example.com"):
    store = InMemoryGoogleCredentialStore()
    store.save(GoogleCredentialRecord("google-account", email, "access", "refresh", None, ("gmail",)))
    return GoogleCredentialBroker("unused", store, lambda *_a, **_k: None, lambda _c: email)


def _service(failure=None, fail_receipt=False, callback=None):
    history = InMemoryDailyBriefHistoryStore()
    history.save(DailyBriefRun("run-1", "2026-01-01", "2026-01-01", "success", "hello", "calendar", "mail", "web", "scouts"))
    provider = FakeEmailSendExecutor(failure=failure, on_effect=callback)
    service, provider = build_offline_delivery_service(history, _broker(), InMemoryDeliveryStore(fail_receipt), provider)
    return service, provider


def test_email_send_is_closed_typed_and_requires_self_identity():
    assert EMAIL_SEND_ACTIONS == {"email.send"}
    assert EXTERNAL_READ_ACTIONS == {"email.read", "calendar.read", "web.search"}
    with pytest.raises(ValueError):
        EmailSendDescriptor({"provider_ref": "google.gmail.v1", "account_ref": "a", "recipient": "other@example.com"},
                            {"to": "owner@example.com", "subject": "s", "body_text": "b", "daily_brief_run_id": "r"})


def test_approval_exact_fields_and_attempt_before_fake_effect():
    seen = []
    service, provider = _service(callback=lambda: seen.append(service.store.get_attempt("delivery-1")))
    proposal = service.propose("run-1", "delivery-1")
    with pytest.raises(PermissionError, match="approval_required"):
        service.send(proposal)
    service.approve(proposal)
    receipt = service.send(proposal)
    assert receipt.status == "provider_accepted"
    assert seen and seen[0].status == "attempting"
    assert service.store.get_attempt("delivery-1").content_digest == proposal.content_digest
    assert not hasattr(receipt, "body_text")
    assert provider.requests[0][1]["to"] == proposal.to


def test_mutation_after_approval_is_rejected_and_mime_is_text_only():
    service, provider = _service()
    proposal = service.propose("run-1", "delivery-1")
    service.approve(proposal)
    mutated = proposal.__class__(proposal.delivery_id, proposal.daily_brief_run_id, proposal.provider_ref, proposal.account_ref,
                                 proposal.to, proposal.subject + " changed", proposal.body_text, proposal.content_digest)
    with pytest.raises(PermissionError, match="approval_required"):
        service.send(mutated)
    assert not provider.requests
    assert b"Content-Type: text/plain" in (service.send(proposal) and provider.mimes[0])
    with pytest.raises(ValueError):
        EmailSendDescriptor({"provider_ref": "google.gmail.v1", "account_ref": "a", "recipient": "owner@example.com"},
                            {"to": "owner@example.com", "subject": "bad\nBcc: x", "body_text": "b", "daily_brief_run_id": "r"})


def test_fake_failure_has_no_receipt_and_no_retry_or_401_recovery():
    service, provider = _service(failure="credential_rejected")
    proposal = service.propose("run-1", "delivery-1")
    service.approve(proposal)
    result = service.send(proposal)
    assert not result.ok
    assert service.state("delivery-1") == "ambiguous"
    assert not service.store.receipts
    with pytest.raises(PermissionError, match="ambiguous"):
        service.send(proposal)
    assert len(provider.requests) == 1


def test_receipt_persistence_failure_is_ambiguous_and_completed_run_is_denied():
    service, provider = _service(fail_receipt=True)
    proposal = service.propose("run-1", "delivery-1")
    service.approve(proposal)
    with pytest.raises(OSError, match="receipt_persistence_failed"):
        service.send(proposal)
    assert service.state("delivery-1") == "ambiguous"
    service2, _provider2 = _service()
    proposal2 = service2.propose("run-1", "delivery-1")
    service2.approve(proposal2)
    service2.send(proposal2)
    with pytest.raises(PermissionError, match="already_completed"):
        service2.send(proposal2)


def test_source_is_persisted_daily_brief_and_no_llm_or_network_boundary():
    service, provider = _service()
    proposal = service.propose("run-1", "delivery-1")
    assert proposal.body_text.startswith("IAOS — Resumo diário\n1 de janeiro de 2026")
    assert provider.requests == []
    assert "external.write" not in EMAIL_SEND_ACTIONS
