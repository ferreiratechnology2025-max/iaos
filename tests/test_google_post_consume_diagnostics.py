from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path

import pytest

from application.google_connection import (
    CALENDAR_SCOPE, GoogleConnectionError, GoogleCredentialBroker,
    InMemoryGoogleCredentialStore,
)
from application.google_oauth_service import (
    GoogleOAuthService, OAuthConfig, _recover_equivalent_scope_warning,
)
from application.google_secure_store import WindowsDPAPIGoogleCredentialStore
from application.oauth_transaction_store import InMemoryOAuthTransactionStore


RED_FLAG_VALUES = (
    "AUTHORIZATION_CODE_SENTINEL", "ACCESS_TOKEN_SENTINEL",
    "REFRESH_TOKEN_SENTINEL", "ID_TOKEN_SENTINEL", "CLIENT_SECRET_SENTINEL",
)


class _Credentials:
    token = "ACCESS_TOKEN_SENTINEL"
    refresh_token = "REFRESH_TOKEN_SENTINEL"
    id_token = "ID_TOKEN_SENTINEL"
    expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)
    scopes = ("openid", "email", CALENDAR_SCOPE)


class _FakeFlow:
    client_config = {"client_secret": "test-client-secret"}

    def __init__(self):
        self.credentials = _Credentials()
        self.failure = None
        self.oauth2session = type("Session", (), {"token": None})()

    def authorization_url(self, **kwargs):
        return "https://accounts.google.example/auth?state=" + kwargs["state"], kwargs["state"]

    def fetch_token(self, **kwargs):
        if self.failure:
            raise self.failure("token exchange failed")


class _FailingCredentialStore(InMemoryGoogleCredentialStore):
    def save(self, record):
        raise OSError("storage backend unavailable")


def _service(tmp_path: Path, credential_store=None, identity_fetcher=None, flow_failure=None):
    client = tmp_path / "client.json"
    client.write_text("{}", encoding="utf-8")
    broker = GoogleCredentialBroker(
        str(client), credential_store or InMemoryGoogleCredentialStore(),
        lambda *_a, **_k: None, identity_fetcher or (lambda _credentials: "person@example.com"),
    )
    service = GoogleOAuthService(
        OAuthConfig(client, "http://127.0.0.1:8766/integrations/google/callback"), broker,
        transaction_store=InMemoryOAuthTransactionStore(),
    )
    flow = _FakeFlow()
    flow.failure = flow_failure
    service._new_flow = lambda state=None, scopes=None: flow
    return service, broker


def _begin(service):
    return service.begin().split("state=", 1)[1]


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_post_consume_happy_path_dpapi_and_profile(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")
    credential_store = WindowsDPAPIGoogleCredentialStore(tmp_path / "connection.dpapi")
    service, broker = _service(tmp_path, credential_store=credential_store)

    state = _begin(service)
    view = service.callback(state, "AUTHORIZATION_CODE_SENTINEL")

    assert view.connected and view.calendar and not view.gmail
    assert credential_store.load() is not None
    assert "token_exchange_result=PASS" in caplog.text
    assert "identity_validation_result=PASS" in caplog.text
    assert "granted_scopes_validation=PASS" in caplog.text
    assert "credential_profile_build=PASS" in caplog.text
    assert "dpapi_save_result=PASS" in caplog.text
    assert "dpapi_readback_result=PASS" in caplog.text
    assert not any(value in caplog.text for value in RED_FLAG_VALUES)


def test_token_exchange_failure_is_typed_and_does_not_persist(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")
    service, broker = _service(tmp_path, flow_failure=RuntimeError)

    with pytest.raises(GoogleConnectionError, match="oauth_callback_failed"):
        service.callback(_begin(service), "AUTHORIZATION_CODE_SENTINEL")

    assert "token_exchange_result=FAIL" in caplog.text
    assert "token_exchange_error_class=RuntimeError" in caplog.text
    assert broker.status().connected is False


def test_identity_failure_is_typed_and_does_not_persist(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")

    def identity_failure(_credentials):
        raise LookupError("identity lookup failed")

    service, broker = _service(tmp_path, identity_fetcher=identity_failure)
    with pytest.raises(GoogleConnectionError, match="oauth_callback_failed"):
        service.callback(_begin(service), "AUTHORIZATION_CODE_SENTINEL")

    assert "token_exchange_result=PASS" in caplog.text
    assert "identity_validation_result=FAIL" in caplog.text
    assert "identity_validation_error_class=LookupError" in caplog.text
    assert broker.status().connected is False


def test_scope_failure_is_typed_and_does_not_persist(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")
    service, broker = _service(tmp_path)
    service._new_flow().credentials.scopes = ("openid", "email", "unexpected.scope")

    with pytest.raises(GoogleConnectionError, match="oauth_callback_failed"):
        service.callback(_begin(service), "AUTHORIZATION_CODE_SENTINEL")

    assert "granted_scopes_validation=FAIL" in caplog.text
    assert "unexpected_scope_present=YES" in caplog.text
    assert broker.status().connected is False


def test_dpapi_save_failure_is_typed_and_does_not_persist(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")
    service, broker = _service(tmp_path, credential_store=_FailingCredentialStore())

    with pytest.raises(GoogleConnectionError, match="oauth_callback_failed"):
        service.callback(_begin(service), "AUTHORIZATION_CODE_SENTINEL")

    assert "dpapi_save_result=FAIL" in caplog.text
    assert "dpapi_save_error_class=OSError" in caplog.text
    assert broker.status().connected is False


def test_duplicate_callback_cannot_delete_successful_connection(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")
    service, broker = _service(tmp_path)
    state = _begin(service)
    first = service.callback(state, "AUTHORIZATION_CODE_SENTINEL")
    assert first.connected

    with pytest.raises(GoogleConnectionError, match="oauth_state_invalid_or_expired"):
        service.callback(state, "AUTHORIZATION_CODE_SENTINEL")

    after = broker.status()
    assert after.connected and after.email == "person@example.com" and after.calendar
    assert "state_already_consumed=YES" in caplog.text
    assert not any(value in caplog.text for value in RED_FLAG_VALUES)


def _scope_warning(old_scope, new_scope, token=None):
    warning = Warning("scope changed")
    warning.old_scope = old_scope
    warning.new_scope = new_scope
    warning.token = token
    return warning


def test_equivalent_google_email_scope_warning_is_recovered():
    token = {"access_token": "ACCESS_TOKEN_SENTINEL"}
    warning = _scope_warning(
        ("openid", "email", CALENDAR_SCOPE),
        ("openid", "https://www.googleapis.com/auth/userinfo.email", CALENDAR_SCOPE),
        token,
    )

    assert _recover_equivalent_scope_warning(warning) is token


@pytest.mark.parametrize(
    "warning",
    [
        _scope_warning(("openid", "email"), ("openid", "email"), {"access_token": "x"}),
        _scope_warning(
            ("email", CALENDAR_SCOPE),
            ("https://www.googleapis.com/auth/userinfo.email", CALENDAR_SCOPE),
            {"access_token": "x"},
        ),
        _scope_warning(
            ("openid", "email"),
            ("openid", "https://www.googleapis.com/auth/userinfo.email"),
            {"access_token": "x"},
        ),
        _scope_warning(
            ("openid", "email", CALENDAR_SCOPE),
            ("openid", "email", CALENDAR_SCOPE, "https://www.googleapis.com/auth/gmail.readonly"),
            {"access_token": "x"},
        ),
        _scope_warning(
            ("openid", "email", CALENDAR_SCOPE),
            ("openid", "email", CALENDAR_SCOPE, "https://www.googleapis.com/auth/calendar"),
            {"access_token": "x"},
        ),
        _scope_warning(
            ("openid", "email", CALENDAR_SCOPE),
            ("openid", "email", CALENDAR_SCOPE, "https://www.googleapis.com/auth/drive.readonly"),
            {"access_token": "x"},
        ),
        _scope_warning(
            ("openid", "email", CALENDAR_SCOPE),
            ("openid", "email", CALENDAR_SCOPE),
            None,
        ),
        Warning("unrelated warning"),
    ],
)
def test_unaccepted_warning_fails_closed(warning):
    assert _recover_equivalent_scope_warning(warning) is None


def test_exact_scopes_do_not_require_warning_recovery(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="iaos.google.oauth")
    service, broker = _service(tmp_path)

    view = service.callback(_begin(service), "AUTHORIZATION_CODE_SENTINEL")

    assert view.connected
    assert "token_exchange_result=PASS scope_alias_normalized=NO" in caplog.text
    assert "granted_scopes_validation=PASS" in caplog.text
