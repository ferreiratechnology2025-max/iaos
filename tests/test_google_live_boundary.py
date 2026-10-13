from __future__ import annotations

import http.client
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from application.google_connection import (
    CALENDAR_SCOPE,
    GMAIL_SCOPE,
    GoogleConnectionError,
    GoogleCredentialBroker,
    GoogleCredentialRecord,
    InMemoryGoogleCredentialStore,
)
from application.google_integration_server import GoogleIntegrationServer
from application.google_oauth_service import GoogleOAuthService, OAuthConfig
from application.oauth_transaction_store import (
    OAuthTransaction, WindowsDPAPIOAuthTransactionStore, state_digest,
)
from application.google_secure_store import WindowsDPAPIGoogleCredentialStore


def test_dpapi_store_round_trips_across_store_instances_without_plaintext(tmp_path):
    if os.name != "nt":
        pytest.skip("DPAPI is Windows-only")
    path = tmp_path / "google.connection.dpapi"
    record = GoogleCredentialRecord("internal", "person@example.com", "short-token", "refresh-secret", 4102444800, ("openid", CALENDAR_SCOPE))
    first = WindowsDPAPIGoogleCredentialStore(path)
    first.save(record)
    assert b"short-token" not in path.read_bytes()
    assert b"refresh-secret" not in path.read_bytes()
    second_store = WindowsDPAPIGoogleCredentialStore(path)
    assert second_store.load() == record
    restarted = GoogleCredentialBroker("internal", second_store, lambda *_a, **_k: None, lambda _c: record.email)
    state = restarted.status()
    assert state.connected and state.email == record.email and state.calendar
    second_store.delete()
    assert not path.exists()


class _FakeFlow:
    client_config = {"client_secret": "test-client-secret"}
    credentials = type("Credentials", (), {
        "token": "access-token", "refresh_token": "refresh-token",
        "expiry": datetime(2030, 1, 1, tzinfo=timezone.utc),
    })()
    seen = None

    @classmethod
    def from_client_secrets_file(cls, path, scopes, **kwargs):
        cls.seen = {"path": path, "scopes": tuple(scopes), **kwargs}
        return cls()

    def authorization_url(self, **kwargs):
        self.auth_kwargs = kwargs
        return "https://accounts.google.example/auth?state=" + kwargs["state"], kwargs["state"]

    def fetch_token(self, **kwargs):
        self.fetch_kwargs = kwargs


def _oauth(tmp_path, transaction_store=None):
    client = tmp_path / "client.json"
    client.write_text("{}", encoding="utf-8")
    store = InMemoryGoogleCredentialStore()
    broker = GoogleCredentialBroker(str(client), store, lambda *_a, **_k: None, lambda _c: "person@example.com")
    service = GoogleOAuthService(
        OAuthConfig(client, "http://127.0.0.1:8766/integrations/google/callback"), broker,
        transaction_store=transaction_store,
    )
    created = []
    service._new_flow = lambda state=None, scopes=None: created.append(_FakeFlow()) or created[-1]  # test-only provider seam
    return service, broker, created


def test_oauth_state_is_pkce_bound_and_one_shot(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")
    service, broker, created = _oauth(tmp_path)
    url = service.begin()
    assert "state=" in url
    state = url.split("state=", 1)[1]
    view = service.callback(state, "authorization-code")
    assert view.connected and view.email == "person@example.com" and view.calendar
    assert _FakeFlow.seen is None  # the test seam bypasses client-file parsing
    assert created[0].auth_kwargs["code_challenge_method"] == "S256"
    assert created[0].auth_kwargs["prompt"] == "select_account"
    assert created[1].fetch_kwargs["code_verifier"]
    assert created[1].fetch_kwargs["include_client_id"] is True
    assert created[1].fetch_kwargs["client_secret"] == "test-client-secret"
    with pytest.raises(Exception, match="oauth_state_invalid_or_expired"):
        service.callback(state, "authorization-code")
    assert "state_lookup=HIT" in caplog.text
    assert "state_already_consumed=YES" in caplog.text


def test_incremental_gmail_reuses_identity_account_ref_and_refresh_token(tmp_path):
    service, broker, created = _oauth(tmp_path)
    broker.store_credentials(_FakeFlow.credentials, "person@example.com", (CALENDAR_SCOPE,))
    before = broker._store.load()

    url = service.begin(("calendar", "gmail"))
    state = url.split("state=", 1)[1]
    view = service.callback(state, "authorization-code")
    after = broker._store.load()

    assert view.connected and view.calendar and view.gmail
    assert before is not None and after is not None
    assert after.account_ref == before.account_ref
    assert after.email == before.email
    assert after.refresh_token == before.refresh_token
    assert GMAIL_SCOPE in after.scopes


def test_incremental_identity_mismatch_preserves_calendar_connection(tmp_path):
    service, broker, _created = _oauth(tmp_path)
    broker.store_credentials(_FakeFlow.credentials, "person@example.com", (CALENDAR_SCOPE,))
    before = broker.status()
    service._broker._identity_fetcher = lambda _credentials: "other@example.com"

    state = service.begin(("calendar", "gmail")).split("state=", 1)[1]
    with pytest.raises(Exception, match="oauth_identity_mismatch"):
        service.callback(state, "authorization-code")

    after = broker.status()
    assert before == after and after.calendar and not after.gmail


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_oauth_state_survives_service_reinstantiation(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")
    transaction_store = WindowsDPAPIOAuthTransactionStore(tmp_path / "transactions")
    first, broker, _created = _oauth(tmp_path, transaction_store)
    url = first.begin()
    state = url.split("state=", 1)[1]

    second, _broker, created = _oauth(tmp_path, WindowsDPAPIOAuthTransactionStore(tmp_path / "transactions"))
    second.callback(state, "authorization-code")
    assert created[0].fetch_kwargs["code_verifier"]

    assert "state_lookup=HIT" in caplog.text


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_oauth_transaction_expiry_and_one_shot(tmp_path):
    store = WindowsDPAPIOAuthTransactionStore(tmp_path / "transactions")
    transaction = OAuthTransaction(
        state_digest=state_digest("one-shot-state"), pkce_verifier="verifier-secret",
        issued_at=100.0, expires_at=110.0,
        requested_capabilities=("calendar",),
        redirect_uri="http://127.0.0.1:8766/integrations/google/callback",
    )
    store.save(transaction)
    assert store.consume(transaction.state_digest, 109.0) == transaction
    assert store.consume(transaction.state_digest, 109.0) is None

    expired = OAuthTransaction(
        state_digest=state_digest("expired-state"), pkce_verifier="expired-verifier",
        issued_at=100.0, expires_at=110.0,
        requested_capabilities=("calendar",), redirect_uri=transaction.redirect_uri,
    )
    store.save(expired)
    assert store.consume(expired.state_digest, 110.0) is None
    assert store.size() == 0


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_oauth_transaction_concurrent_consume_has_one_winner(tmp_path):
    import concurrent.futures

    path = tmp_path / "transactions"
    first = WindowsDPAPIOAuthTransactionStore(path)
    transaction = OAuthTransaction(
        state_digest=state_digest("concurrent-state"), pkce_verifier="verifier-secret",
        issued_at=100.0, expires_at=200.0, requested_capabilities=("calendar",),
        redirect_uri="http://127.0.0.1:8766/integrations/google/callback",
    )
    first.save(transaction)

    def consume_once():
        return WindowsDPAPIOAuthTransactionStore(path).consume(transaction.state_digest, 101.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: consume_once(), range(8)))
    assert sum(result is not None for result in results) == 1


class _FakeOAuth:
    def begin(self):
        return "https://accounts.google.example/auth?state=public"

    def callback(self, state, code):
        raise AssertionError("callback is not invoked by status test")


class _CallbackOAuth:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def callback(self, state, code):
        self.calls.append((state, code))
        if self.failure:
            raise self.failure
        return object()


def _request(server, method, path):
    c = http.client.HTTPConnection(*server.server_address, timeout=3)
    c.request(method, path)
    response = c.getresponse()
    body = json.loads(response.read())
    c.close()
    return response.status, body


def _redirect_request(server, path):
    c = http.client.HTTPConnection(*server.server_address, timeout=3)
    c.request("GET", path)
    response = c.getresponse()
    response.read()
    location = response.getheader("Location")
    c.close()
    return response.status, location


def test_oauth_callback_redirects_to_settings_without_callback_data():
    store = InMemoryGoogleCredentialStore()
    broker = GoogleCredentialBroker("", store, lambda *_a, **_k: None, lambda _c: "person@example.com")
    oauth = _CallbackOAuth()
    server = GoogleIntegrationServer(("127.0.0.1", 0), broker, oauth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, location = _redirect_request(server, "/integrations/google/callback?state=RAWSTATE&code=RAWCODE")
        assert status == 302
        parsed = urlparse(location)
        assert parsed.path == "/"
        assert parsed.fragment == "/settings/integrations/google"
        assert parsed.query == ""
        assert oauth.calls == [("RAWSTATE", "RAWCODE")]
        assert "RAWSTATE" not in location and "RAWCODE" not in location
    finally:
        server.shutdown()
        server.server_close()


def test_oauth_callback_error_redirects_with_safe_code_only():
    store = InMemoryGoogleCredentialStore()
    broker = GoogleCredentialBroker("", store, lambda *_a, **_k: None, lambda _c: "person@example.com")
    oauth = _CallbackOAuth(GoogleConnectionError("oauth_state_invalid_or_expired"))
    server = GoogleIntegrationServer(("127.0.0.1", 0), broker, oauth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, location = _redirect_request(server, "/integrations/google/callback?state=RAWSTATE&code=RAWCODE")
        parsed = urlparse(location)
        assert status == 302
        assert parsed.path == "/"
        assert parsed.fragment.startswith("/settings/integrations/google?")
        assert parse_qs(parsed.fragment.split("?", 1)[1]) == {"error": ["oauth_state_invalid_or_expired"]}
        assert "RAWSTATE" not in location and "RAWCODE" not in location
    finally:
        server.shutdown()
        server.server_close()


def test_backend_api_is_sanitized_and_gmail_does_not_start_oauth():
    store = InMemoryGoogleCredentialStore()
    broker = GoogleCredentialBroker("", store, lambda *_a, **_k: None, lambda _c: "person@example.com")
    server = GoogleIntegrationServer(("127.0.0.1", 0), broker, _FakeOAuth())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _request(server, "GET", "/integrations/google")
        assert status == 200 and body == {"connected": False, "email": None, "calendar": False, "gmail": False}
        status, body = _request(server, "POST", "/integrations/google/connect")
        assert status == 200 and "authorizationUrl" in body
        status, body = _request(server, "POST", "/integrations/google/capabilities/gmail")
        assert status == 400 and body == {"errorCode": "google_connection_required"}
        assert "token" not in json.dumps(body).lower()
    finally:
        server.shutdown()
        server.server_close()
