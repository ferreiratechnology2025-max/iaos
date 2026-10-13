from datetime import datetime, timezone
import os

import pytest

from application.google_connection import (
    CALENDAR_SCOPE,
    GMAIL_SCOPE,
    GoogleConnectionError,
    GoogleCredentialRecord,
    GoogleCredentialBroker,
    InMemoryGoogleCredentialStore,
)
from application.google_secure_store import SecureCredentialStoreError, WindowsDPAPIGoogleCredentialStore


class Credentials:
    token = "access-token"
    refresh_token = "refresh-token"
    expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)


class Flow:
    def __init__(self):
        self.scopes = None

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        return Credentials()


def broker(flow, store=None):
    return GoogleCredentialBroker(
        "internal-client-secret.json",
        store or InMemoryGoogleCredentialStore(),
        lambda path, scopes: flow,
        lambda credentials: "ferreiratechnology2025@gmail.com",
        account_ref_factory=lambda: "internal-account",
    )


def test_one_click_connect_exposes_only_product_state_and_shares_account():
    flow = Flow()
    b = broker(flow)

    state = b.connect("calendar")
    assert state.connected and state.email == "ferreiratechnology2025@gmail.com"
    assert state.calendar and not state.gmail
    assert "account_ref" not in repr(state)
    assert flow.kwargs["port"] == 0

    state = b.connect("gmail")
    assert state.calendar and state.gmail
    assert b.status().email == "ferreiratechnology2025@gmail.com"


def test_incremental_connect_requests_union_of_existing_and_new_scopes():
    calls = []
    flow = Flow()
    store = InMemoryGoogleCredentialStore()
    b = GoogleCredentialBroker(
        "internal-client-secret.json", store,
        lambda path, scopes: calls.append(tuple(scopes)) or flow,
        lambda credentials: "person@example.com",
        account_ref_factory=lambda: "internal-account",
    )
    b.connect("calendar")
    b.connect("gmail")
    assert set(calls[0]) >= {CALENDAR_SCOPE}
    assert set(calls[1]) >= {CALENDAR_SCOPE, GMAIL_SCOPE}


def test_disconnect_removes_all_capabilities():
    b = broker(Flow())
    b.connect("calendar")
    b.disconnect()
    assert b.status().connected is False
    assert b.lookup("internal-account", "google.calendar.v3") is None


def test_unknown_capability_fails_closed():
    with pytest.raises(GoogleConnectionError, match="unsupported_capability"):
        broker(Flow()).connect("drive")


def test_expired_credential_is_refreshed_and_persisted_automatically():
    store = InMemoryGoogleCredentialStore()
    b = GoogleCredentialBroker(
        "internal-client-secret.json", store,
        lambda path, scopes: Flow(),
        lambda credentials: "person@example.com",
        refresh=lambda old: GoogleCredentialRecord(
            old.account_ref, old.email, "refreshed-token", old.refresh_token,
            4102444800, old.scopes,
        ),
        account_ref_factory=lambda: "internal-account",
        clock=lambda: 4102444700,
    )
    b.connect("calendar")
    # Force an expired record while keeping the original refresh token.
    old = store.load()
    store.save(GoogleCredentialRecord(old.account_ref, old.email, old.access_token,
                                      old.refresh_token, 1, old.scopes))
    profile = b.lookup("internal-account", "google.calendar.v3")
    assert profile.access_token == "refreshed-token"
    assert store.load().access_token == "refreshed-token"


def test_provider_omitting_refresh_token_preserves_existing_refresh_token():
    store = InMemoryGoogleCredentialStore()
    b = broker(Flow(), store)
    b.store_credentials(Credentials(), "person@example.com", (CALENDAR_SCOPE,))

    class AccessOnlyCredentials:
        token = "new-access-token"
        refresh_token = None
        expiry = datetime(2031, 1, 1, tzinfo=timezone.utc)

    b.store_credentials(AccessOnlyCredentials(), "person@example.com", (CALENDAR_SCOPE,))
    record = store.load()
    assert record.access_token == "new-access-token"
    assert record.refresh_token == "refresh-token"


def _secure_record():
    return GoogleCredentialRecord(
        "internal-account", "person@example.com", "ACCESS_TOKEN_SENTINEL",
        "REFRESH_TOKEN_SENTINEL", 4102444800, ("openid", CALENDAR_SCOPE),
    )


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_round_trip_is_encrypted_and_deletable(tmp_path):
    path = tmp_path / "google.connection.dpapi"
    record = _secure_record()
    store = WindowsDPAPIGoogleCredentialStore(path)

    store.save(record)

    blob = path.read_bytes()
    assert blob
    assert b"ACCESS_TOKEN_SENTINEL" not in blob
    assert b"REFRESH_TOKEN_SENTINEL" not in blob
    assert store.load() == record
    store.delete()
    assert store.load() is None
    assert not path.exists()


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_corrupted_blob_fails_closed_without_secret_in_observable_error(tmp_path):
    path = tmp_path / "google.connection.dpapi"
    store = WindowsDPAPIGoogleCredentialStore(path)
    store.save(_secure_record())
    path.write_bytes(b"ACCESS_TOKEN_SENTINEL corrupted")

    with pytest.raises(SecureCredentialStoreError) as error:
        store.load()
    assert str(error.value) == "secure_credential_unreadable"
    assert "ACCESS_TOKEN_SENTINEL" not in str(error.value)
    assert "ACCESS_TOKEN_SENTINEL" not in repr(error.value)


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_missing_blob_is_disconnected(tmp_path):
    store = WindowsDPAPIGoogleCredentialStore(tmp_path / "missing.dpapi")
    assert store.load() is None


def test_secure_store_rejects_unsupported_platform_deterministically(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(SecureCredentialStoreError, match="^windows_dpapi_unavailable$"):
        WindowsDPAPIGoogleCredentialStore(tmp_path / "connection.dpapi")


def test_secure_store_rejects_missing_localappdata_deterministically(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(SecureCredentialStoreError, match="^local_app_data_unavailable$"):
        WindowsDPAPIGoogleCredentialStore(tmp_path / "connection.dpapi")


def test_production_composition_selects_secure_store(monkeypatch):
    import application.google_integration_service as composition

    selected = {}

    class Store:
        def __init__(self):
            selected["store"] = self

    class Broker:
        def __init__(self, _client_secret, store, **_kwargs):
            selected["broker_store"] = store

        def set_refresh_adapter(self, _refresh):
            pass

    class OAuth:
        def __init__(self, *_args, **_kwargs):
            pass

        def refresh(self, record):
            return record

    class Scouts:
        def __init__(self, *_args, **_kwargs):
            pass

        def unconsumed_findings(self, *_args, **_kwargs):
            return ()

        def start(self):
            pass

    class Routine:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    class Server:
        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(composition, "WindowsDPAPIGoogleCredentialStore", Store)
    monkeypatch.setattr(composition, "GoogleCredentialBroker", Broker)
    monkeypatch.setattr(composition, "GoogleOAuthService", OAuth)
    monkeypatch.setattr(composition, "WindowsDPAPIOAuthTransactionStore", lambda: object())
    monkeypatch.setattr(composition, "ScoutService", Scouts)
    monkeypatch.setattr(composition, "DailyBriefRoutineService", Routine)
    monkeypatch.setattr(composition, "GoogleIntegrationServer", Server)
    monkeypatch.setattr(composition, "configured_agnes_provider", lambda: object())
    monkeypatch.setattr(composition, "JsonDailyBriefHistoryStore", lambda: object())
    monkeypatch.setattr(composition, "JsonDailyBriefRoutineStore", lambda: object())
    monkeypatch.setattr(composition, "JsonScoutStore", lambda: object())
    monkeypatch.setattr(composition, "JsonScoutHistoryStore", lambda: object())

    composition.build_server()

    assert selected["broker_store"] is selected["store"]
