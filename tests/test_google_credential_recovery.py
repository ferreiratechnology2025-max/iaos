from __future__ import annotations

import json

import httpx
import pytest

from application.google_connection import (
    GoogleCredentialBroker,
    GoogleCredentialRecord,
    InMemoryGoogleCredentialStore,
)
from governance.aep_executors import (
    CredentialProfile,
    GoogleCalendarConnectorError,
    GoogleCalendarReadExecutor,
    GoogleGmailConnectorError,
    GoogleGmailReadExecutor,
)


ACCOUNT = "account-1"
EMAIL = "user@example.com"
CALENDAR_RESOURCE = json.dumps({
    "provider_ref": "google.calendar.v3", "account_ref": ACCOUNT, "calendar_ref": "primary",
})
CALENDAR_ARGS = (("request", json.dumps({
    "start": "2026-10-06T00:00:00Z", "end": "2026-10-07T00:00:00Z",
    "limit": 1, "fields": "items(id,start,end,summary,location)",
})),)
GMAIL_RESOURCE = json.dumps({
    "provider_ref": "google.gmail.v1", "account_ref": ACCOUNT, "mailbox_ref": "me",
})
GMAIL_ARGS = (("request", json.dumps({
    "query": "", "time_window": {"after": "2026-10-06", "before": "2026-10-07"},
    "limit": 1, "fields": ["message_ref", "received_at", "sender", "subject", "snippet"],
})),)


def _broker(with_refresh_token=True, refresh_failure=False):
    store = InMemoryGoogleCredentialStore()
    current = GoogleCredentialRecord(
        ACCOUNT, EMAIL, "old-access", "refresh-token" if with_refresh_token else None,
        4102444800, ("https://www.googleapis.com/auth/calendar.readonly",
                      "https://www.googleapis.com/auth/gmail.readonly"),
    )
    store.save(current)
    refresh_calls = []
    broker = GoogleCredentialBroker(
        "unused", store, lambda *_a, **_k: None, lambda _credentials: EMAIL,
    )

    def refresh(record):
        refresh_calls.append(record.access_token)
        if refresh_failure:
            raise RuntimeError("refresh fixture failure")
        return GoogleCredentialRecord(
            record.account_ref, record.email, "new-access", None, 4102444800, record.scopes,
        )

    broker.set_refresh_adapter(refresh)
    return broker, store, refresh_calls


def test_calendar_401_refreshes_once_replays_same_request_and_preserves_refresh_token():
    calls = []
    broker, store, refresh_calls = _broker()

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"items": []})

    result = GoogleCalendarReadExecutor(broker, transport=httpx.MockTransport(handle)).run(
        CALENDAR_RESOURCE, CALENDAR_ARGS, None,
    )

    assert result.content == {"events": [], "truncated": False}
    assert len(calls) == 2
    assert refresh_calls == ["old-access"]
    assert calls[0].headers["authorization"] == "Bearer old-access"
    assert calls[1].headers["authorization"] == "Bearer new-access"
    assert store.load().refresh_token == "refresh-token"


def test_gmail_401_refreshes_once_and_zero_messages_is_success():
    calls = []
    broker, store, refresh_calls = _broker()
    executor = GoogleGmailReadExecutor(broker, transport=httpx.MockTransport(
        lambda request: calls.append(request) or (
            httpx.Response(401, json={"error": "invalid_token"}) if len(calls) == 1
            else httpx.Response(200, json={"messages": []})
        )
    ))
    executor.begin_execution("gmail-recovery")
    try:
        result = executor.run(GMAIL_RESOURCE, GMAIL_ARGS, None)
    finally:
        executor.end_execution()

    assert result.content == {"items": [], "truncated": False}
    assert len(calls) == 2
    assert refresh_calls == ["old-access"]
    assert store.load().refresh_token == "refresh-token"
    assert executor.last_execution_metrics["list_request_count"] == 1
    assert executor.last_execution_metrics["detail_request_count"] == 0


def test_second_401_fails_closed_after_one_refresh_and_replay():
    calls = []
    broker, _store, refresh_calls = _broker()
    executor = GoogleGmailReadExecutor(broker, transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(401, json={"error": "invalid_token"})
    ))
    executor.begin_execution("gmail-second-401")
    try:
        with pytest.raises(GoogleGmailConnectorError, match="credential_rejected"):
            executor.run(GMAIL_RESOURCE, GMAIL_ARGS, None)
    finally:
        executor.end_execution()

    assert len(calls) == 2
    assert len(refresh_calls) == 1


def test_401_without_refresh_token_fails_closed_without_replay():
    calls = []
    broker, _store, refresh_calls = _broker(with_refresh_token=False)
    executor = GoogleCalendarReadExecutor(broker, transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(401, json={"error": "invalid_token"})
    ))

    with pytest.raises(GoogleCalendarConnectorError, match="credential_rejected"):
        executor.run(CALENDAR_RESOURCE, CALENDAR_ARGS, None)

    assert len(calls) == 1
    assert refresh_calls == []


def test_refresh_failure_fails_closed_without_replay():
    calls = []
    broker, _store, refresh_calls = _broker(refresh_failure=True)
    executor = GoogleCalendarReadExecutor(broker, transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(401, json={"error": "invalid_token"})
    ))

    with pytest.raises(GoogleCalendarConnectorError, match="credential_refresh_failed"):
        executor.run(CALENDAR_RESOURCE, CALENDAR_ARGS, None)

    assert len(calls) == 1
    assert refresh_calls == ["old-access"]


@pytest.mark.parametrize("status", [403, 429, 500])
def test_non_401_provider_failures_do_not_refresh_or_replay(status):
    calls = []
    broker, _store, refresh_calls = _broker()
    executor = GoogleCalendarReadExecutor(broker, transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(status, json={"error": "failure"})
    ))

    with pytest.raises(GoogleCalendarConnectorError):
        executor.run(CALENDAR_RESOURCE, CALENDAR_ARGS, None)

    assert len(calls) == 1
    assert refresh_calls == []
