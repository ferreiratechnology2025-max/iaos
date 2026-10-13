import base64
import json

import httpx
import pytest

from governance.aep_executors import (
    CredentialProfile,
    GoogleGmailSendConnectorError,
    GoogleGmailSendExecutor,
    MappingCredentialResolver,
)


PROVIDER = "google.gmail.v1"
ACCOUNT = "account-1"
RESOURCE = json.dumps({"provider_ref": PROVIDER, "account_ref": ACCOUNT, "recipient": "owner@example.com"}, sort_keys=True, separators=(",", ":"))
REQUEST = json.dumps({"to": "owner@example.com", "subject": "IAOS brief", "body_text": "hello\nworld", "daily_brief_run_id": "brief-1"}, sort_keys=True, separators=(",", ":"))
ARGS = (("request", REQUEST),)


def _executor(transport, scopes=("https://www.googleapis.com/auth/gmail.send",)):
    resolver = MappingCredentialResolver({(
        ACCOUNT, PROVIDER
    ): CredentialProfile(PROVIDER, ACCOUNT, "owner@example.com", "access-token", 4102444800, tuple(scopes))})
    return GoogleGmailSendExecutor(resolver, transport=transport, clock=lambda: 0)


def test_send_posts_exact_endpoint_once_and_maps_raw_text_mime():
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        raw = base64.urlsafe_b64decode(payload["raw"] + "=" * (-len(payload["raw"]) % 4))
        assert b"Content-Type: text/plain" in raw
        assert b"To: owner@example.com" in raw
        assert b"Subject: IAOS brief" in raw
        assert b"hello\nworld" in raw
        return httpx.Response(200, json={"id": "provider-message-1"})

    result = _executor(httpx.MockTransport(handler)).run(RESOURCE, ARGS, None)
    assert result.content == {"provider_message_id": "provider-message-1"}
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert str(calls[0].url) == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def test_readonly_scope_is_rejected_before_network():
    calls = []
    executor = _executor(httpx.MockTransport(lambda request: calls.append(request)), scopes=("https://www.googleapis.com/auth/gmail.readonly",))
    with pytest.raises(GoogleGmailSendConnectorError, match="scope_missing"):
        executor.run(RESOURCE, ARGS, None)
    assert calls == []


@pytest.mark.parametrize("status,code", [(401, "credential_rejected"), (429, "provider_rate_limit"), (500, "provider_unavailable")])
def test_provider_failures_are_single_attempts_without_recovery(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "opaque"})

    executor = _executor(httpx.MockTransport(handler))
    with pytest.raises(GoogleGmailSendConnectorError, match=code):
        executor.run(RESOURCE, ARGS, None)
    assert len(calls) == 1


def test_timeout_is_ambiguous_signal_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")

    executor = _executor(httpx.MockTransport(handler))
    with pytest.raises(GoogleGmailSendConnectorError, match="provider_timeout"):
        executor.run(RESOURCE, ARGS, None)
    assert len(calls) == 1


def test_header_injection_and_wrong_provider_fail_closed_before_network():
    calls = []
    executor = _executor(httpx.MockTransport(lambda request: calls.append(request)))
    injected = json.dumps({"to": "owner@example.com", "subject": "x\nBcc: bad@example.com", "body_text": "x", "daily_brief_run_id": "brief-1"}, sort_keys=True, separators=(",", ":"))
    with pytest.raises(GoogleGmailSendConnectorError, match="header_injection_rejected"):
        executor.run(RESOURCE, (("request", injected),), None)
    wrong = RESOURCE.replace(PROVIDER, "other.provider")
    with pytest.raises(GoogleGmailSendConnectorError, match="provider_mismatch"):
        executor.run(wrong, ARGS, None)
    assert calls == []
