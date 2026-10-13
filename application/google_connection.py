"""Product-facing Google connection broker.

The UI talks to this module in terms of capabilities (``calendar`` and
``gmail``). Provider identifiers, account references, client-secret paths and
refresh material stay behind this boundary.

The production credential store is deliberately injected. A real deployment
should provide an encrypted OS/service-backed implementation; the in-memory
store below exists only for tests and local composition.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence
from uuid import uuid4

from application.google_calendar_provisioning import IDENTITY_SCOPES
from governance.aep_executors import CredentialProfile, CredentialResolver


CALENDAR_CAPABILITY = "calendar"
GMAIL_CAPABILITY = "gmail"
SUPPORTED_CAPABILITIES = (CALENDAR_CAPABILITY, GMAIL_CAPABILITY)
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
CAPABILITY_SCOPES = {
    CALENDAR_CAPABILITY: (CALENDAR_SCOPE,),
    GMAIL_CAPABILITY: (GMAIL_SCOPE,),
    "gmail_send": (GMAIL_SEND_SCOPE,),
}
CALENDAR_PROVIDER_REF = "google.calendar.v3"
GMAIL_PROVIDER_REF = "google.gmail.v1"
CAPABILITY_PROVIDER_REFS = {
    CALENDAR_CAPABILITY: CALENDAR_PROVIDER_REF,
    GMAIL_CAPABILITY: GMAIL_PROVIDER_REF,
}


class GoogleConnectionError(Exception):
    """Safe, user-displayable connection failure without secret details."""


@dataclass(frozen=True)
class GoogleCredentialRecord:
    """Opaque stored credential material; never returned to the UI."""

    account_ref: str
    email: str
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[float]
    scopes: tuple[str, ...]


class GoogleCredentialStore(Protocol):
    def load(self) -> Optional[GoogleCredentialRecord]: ...
    def save(self, record: GoogleCredentialRecord) -> None: ...
    def delete(self) -> None: ...


class InMemoryGoogleCredentialStore:
    """Test/local double. Do not use as a production persistence backend."""

    def __init__(self) -> None:
        self._record: Optional[GoogleCredentialRecord] = None

    def load(self) -> Optional[GoogleCredentialRecord]:
        return self._record

    def save(self, record: GoogleCredentialRecord) -> None:
        self._record = record

    def delete(self) -> None:
        self._record = None


@dataclass(frozen=True)
class GoogleConnectionView:
    """The complete state shape exposed to Settings > Integrations > Google."""

    connected: bool
    email: Optional[str]
    calendar: bool
    gmail: bool


class GoogleCredentialBroker(CredentialResolver):
    """One-click product connection with incremental Google permissions."""

    def __init__(
        self,
        client_secret_file: str,
        store: GoogleCredentialStore,
        flow_factory: Callable[..., Any],
        identity_fetcher: Callable[[Any], str],
        refresh: Optional[Callable[[GoogleCredentialRecord], GoogleCredentialRecord]] = None,
        account_ref_factory: Callable[[], str] = lambda: "google-" + uuid4().hex,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client_secret_file = client_secret_file
        self._store = store
        self._flow_factory = flow_factory
        self._identity_fetcher = identity_fetcher
        self._refresh = refresh
        self._account_ref_factory = account_ref_factory
        self._clock = clock

    def set_refresh_adapter(self, refresh: Callable[[GoogleCredentialRecord], GoogleCredentialRecord]) -> None:
        """Attach the provider refresh implementation after service wiring."""
        self._refresh = refresh

    def store_credentials(self, credentials: Any, email: str, scopes: Sequence[str]) -> None:
        token = getattr(credentials, "token", None)
        if not isinstance(email, str) or not email:
            raise GoogleConnectionError("identity_missing")
        if not isinstance(token, str) or not token:
            raise GoogleConnectionError("access_token_missing")
        expiry = getattr(credentials, "expiry", None)
        expires_at = expiry.timestamp() if isinstance(expiry, datetime) else None
        current = self._store.load()
        merged_scopes = set(scopes) | set(IDENTITY_SCOPES)
        if current:
            merged_scopes.update(current.scopes)
        self._store.save(GoogleCredentialRecord(
            account_ref=current.account_ref if current else self._account_ref_factory(),
            email=email, access_token=token,
            refresh_token=getattr(credentials, "refresh_token", None) or (current.refresh_token if current else None),
            expires_at=expires_at, scopes=tuple(sorted(merged_scopes)),
        ))

    def status(self) -> GoogleConnectionView:
        record = self._store.load()
        if record is None:
            return GoogleConnectionView(False, None, False, False)
        scopes = set(record.scopes)
        return GoogleConnectionView(
            connected=True,
            email=record.email,
            calendar=CALENDAR_SCOPE in scopes,
            gmail=GMAIL_SCOPE in scopes,
        )

    def connect(self, capability: str) -> GoogleConnectionView:
        self._require_capability(capability)
        current = self._store.load()
        scopes = set(IDENTITY_SCOPES)
        if current:
            scopes.update(current.scopes)
        scopes.update(CAPABILITY_SCOPES[capability])
        try:
            flow = self._flow_factory(self._client_secret_file, scopes=sorted(scopes))
            credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
            email = self._identity_fetcher(credentials)
            token = getattr(credentials, "token", None)
            if not isinstance(email, str) or not email:
                raise GoogleConnectionError("identity_missing")
            if not isinstance(token, str) or not token:
                raise GoogleConnectionError("access_token_missing")
            expiry = getattr(credentials, "expiry", None)
            expires_at = expiry.timestamp() if isinstance(expiry, datetime) else None
            self.store_credentials(credentials, email, tuple(scopes))
            return self.status()
        except GoogleConnectionError:
            raise
        except Exception as exc:
            raise GoogleConnectionError("oauth_failed") from exc

    def disconnect(self) -> None:
        self._store.delete()

    def refresh_after_provider_rejection(self, account_ref: str, provider_ref: str) -> bool:
        """Refresh one rejected Google credential without changing its authority."""
        record = self._store.load()
        capability = next((key for key, value in CAPABILITY_PROVIDER_REFS.items() if value == provider_ref), None)
        if record is None or capability is None or record.account_ref != account_ref:
            return False
        if not set(CAPABILITY_SCOPES[capability]).issubset(set(record.scopes)):
            return False
        if not record.refresh_token or self._refresh is None:
            return False
        refreshed = self._refresh(record)
        if not isinstance(refreshed, GoogleCredentialRecord):
            raise GoogleConnectionError("credential_refresh_invalid")
        if refreshed.account_ref != record.account_ref or refreshed.email != record.email:
            raise GoogleConnectionError("credential_refresh_identity_mismatch")
        if not refreshed.access_token:
            raise GoogleConnectionError("credential_refresh_access_token_missing")
        self._store.save(GoogleCredentialRecord(
            account_ref=refreshed.account_ref,
            email=refreshed.email,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or record.refresh_token,
            expires_at=refreshed.expires_at,
            scopes=refreshed.scopes,
        ))
        return True

    def lookup(self, account_ref: str, provider_ref: str) -> Optional[CredentialProfile]:
        record = self._store.load()
        capability = next((key for key, value in CAPABILITY_PROVIDER_REFS.items() if value == provider_ref), None)
        if record is None or capability is None or record.account_ref != account_ref:
            return None
        if not set(CAPABILITY_SCOPES[capability]).issubset(set(record.scopes)):
            return None
        if record.expires_at is not None and record.expires_at <= self._clock():
            if not record.refresh_token or self._refresh is None:
                return None
            try:
                refreshed = self._refresh(record)
                if not isinstance(refreshed, GoogleCredentialRecord):
                    raise GoogleConnectionError("credential_refresh_invalid")
                if refreshed.account_ref != record.account_ref or refreshed.email != record.email:
                    raise GoogleConnectionError("credential_refresh_identity_mismatch")
                self._store.save(refreshed)
                record = refreshed
            except GoogleConnectionError:
                raise
            except Exception as exc:
                raise GoogleConnectionError("credential_refresh_failed") from exc
        return CredentialProfile(
            provider_ref=provider_ref,
            account_ref=record.account_ref,
            provider_account_ref=record.email,
            access_token=record.access_token,
            expires_at=record.expires_at,
            scopes=record.scopes,
        )

    @staticmethod
    def _require_capability(capability: str) -> None:
        if capability not in SUPPORTED_CAPABILITIES:
            raise GoogleConnectionError("unsupported_capability")

