"""Backend-only Google OAuth callback service for Calendar onboarding."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from application.google_connection import (
    CALENDAR_PROVIDER_REF, CALENDAR_SCOPE, CAPABILITY_SCOPES, GMAIL_SCOPE,
    GoogleConnectionError, GoogleConnectionView, GoogleCredentialBroker,
    GoogleCredentialRecord,
)
from application.oauth_transaction_store import (
    InMemoryOAuthTransactionStore, OAuthTransaction, OAuthTransactionStore,
    OAuthTransactionStoreError, state_digest,
)


_LOGGER = logging.getLogger("iaos.google.oauth")

_IDENTITY_EMAIL_SCOPE = "identity.email"
_USERINFO_EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
_CANONICAL_REQUIRED_SCOPES = frozenset({
    "openid", _IDENTITY_EMAIL_SCOPE, "calendar.readonly",
})


def _canonical_scope(scope: str) -> str:
    if scope == "email" or scope == _USERINFO_EMAIL_SCOPE:
        return _IDENTITY_EMAIL_SCOPE
    if scope == "openid":
        return "openid"
    if scope == CALENDAR_SCOPE:
        return "calendar.readonly"
    if scope == GMAIL_SCOPE:
        return "gmail.readonly"
    if scope == "https://www.googleapis.com/auth/gmail.send":
        return "gmail.send"
    return scope


def _canonical_scopes(scopes) -> set[str]:
    return {_canonical_scope(scope) for scope in (scopes or ())}


def _recover_equivalent_scope_warning(warning: Warning, required_scopes=None):
    """Recover only the proven Google email-scope representation variant."""
    token = getattr(warning, "token", None)
    old_scope = getattr(warning, "old_scope", None)
    new_scope = getattr(warning, "new_scope", None)
    if not isinstance(token, dict) or not token.get("access_token"):
        return None
    if not old_scope or not new_scope:
        return None
    if _canonical_scopes(old_scope) != _canonical_scopes(new_scope):
        return None
    expected = required_scopes or _CANONICAL_REQUIRED_SCOPES
    if _canonical_scopes(old_scope) != set(expected):
        return None
    return token


@dataclass(frozen=True)
class OAuthConfig:
    client_secret_file: Path
    redirect_uri: str
    state_ttl_seconds: int = 600


class GoogleOAuthService:
    """Owns OAuth protocol state; exposes only public redirects and views."""

    def __init__(
        self, config: OAuthConfig, broker: GoogleCredentialBroker,
        clock=time.time, transaction_store: Optional[OAuthTransactionStore] = None,
    ) -> None:
        self._config = config
        self._broker = broker
        self._clock = clock
        self._transaction_store = transaction_store or InMemoryOAuthTransactionStore()
        self._consumed_state_digests: set[str] = set()
        self._instance_id = secrets.token_hex(8)
        self._server_instance_id = "unknown"

    def set_server_instance_id(self, instance_id: str) -> None:
        """Attach the owning HTTP server identity for diagnosis-safe logs."""
        self._server_instance_id = instance_id

    def _log_context(self) -> tuple[int, str, str]:
        return os.getpid(), self._server_instance_id, self._instance_id

    def begin(self, capabilities=("calendar",)) -> str:
        self._require_client_secret()
        capabilities = tuple(dict.fromkeys(capabilities))
        if not capabilities or any(capability not in CAPABILITY_SCOPES for capability in capabilities):
            raise GoogleConnectionError("unsupported_capability")
        current = self._broker._store.load()
        if "gmail" in capabilities and current is None:
            raise GoogleConnectionError("google_connection_required")
        requested_scopes = set(("openid", "email"))
        if current:
            requested_scopes.update(current.scopes)
        for capability in capabilities:
            requested_scopes.update(CAPABILITY_SCOPES[capability])
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _pkce_challenge(verifier)
        flow = self._new_flow(scopes=sorted(requested_scopes))
        authorization_url, returned_state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="select_account",
            state=state, code_challenge=challenge, code_challenge_method="S256",
        )
        if returned_state != state:
            raise GoogleConnectionError("oauth_state_creation_failed")
        created_at = self._clock()
        try:
            self._transaction_store.save(OAuthTransaction(
                state_digest=state_digest(state), pkce_verifier=verifier,
                issued_at=created_at,
                expires_at=created_at + self._config.state_ttl_seconds,
                requested_capabilities=capabilities, redirect_uri=self._config.redirect_uri,
            ))
        except OAuthTransactionStoreError as exc:
            raise GoogleConnectionError("oauth_transaction_store_unavailable") from exc
        pid, server_id, instance_id = self._log_context()
        _LOGGER.info(
            "oauth_connect pid=%s server_instance_id=%s service_instance_id=%s "
            "callback_uri=%s state_digest_prefix=%s state_created_at=%.6f "
            "state_expires_at=%.6f state_store_size_after_create=%s "
            "pkce_verifier_present=YES",
            pid, server_id, instance_id, self._config.redirect_uri,
            _digest_prefix(state), created_at,
            created_at + self._config.state_ttl_seconds, self._transaction_store.size(),
        )
        return authorization_url

    def callback(self, state: str, code: str) -> GoogleConnectionView:
        pid, server_id, instance_id = self._log_context()
        full_digest = state_digest(state)
        digest = _digest_prefix(state)
        _LOGGER.info(
            "oauth_callback pid=%s server_instance_id=%s service_instance_id=%s "
            "callback_uri=%s received_state_digest_prefix=%s "
            "state_store_size_before_lookup=%s",
            pid, server_id, instance_id, self._config.redirect_uri,
            digest, self._transaction_store.size(),
        )
        try:
            transaction = self._transaction_store.consume(full_digest, self._clock())
        except OAuthTransactionStoreError as exc:
            raise GoogleConnectionError("oauth_transaction_store_unavailable") from exc
        already_consumed = transaction is None and full_digest in self._consumed_state_digests
        # A consumed record is deleted by design; it is not an expired record.
        # For an unknown digest we fail closed without claiming expiry.
        expired = transaction is None and not already_consumed
        _LOGGER.info(
            "oauth_callback_lookup state_lookup=%s state_expired=%s "
            "state_already_consumed=%s pkce_verifier_present_for_state=%s",
            "HIT" if transaction is not None else "MISS",
            "YES" if expired else "NO",
            "YES" if already_consumed else "NO",
            "YES" if transaction is not None else "NO",
        )
        if transaction is None or expired or transaction.redirect_uri != self._config.redirect_uri:
            raise GoogleConnectionError("oauth_state_invalid_or_expired")
        self._consumed_state_digests.add(full_digest)
        _LOGGER.info("post_consume_stage=START")
        redirect_match = transaction.redirect_uri == self._config.redirect_uri
        _LOGGER.info("redirect_uri_match=%s", "PASS" if redirect_match else "FAIL")
        if not code:
            raise GoogleConnectionError("oauth_code_missing")
        try:
            _LOGGER.info("token_exchange_started=YES")
            requested_scopes = set(("openid", "email"))
            current = self._broker._store.load()
            if current:
                requested_scopes.update(current.scopes)
            for capability in transaction.requested_capabilities:
                requested_scopes.update(CAPABILITY_SCOPES[capability])
            flow = self._new_flow(state=state, scopes=sorted(requested_scopes))
            expected_scopes = _canonical_scopes(requested_scopes)
            try:
                flow.fetch_token(
                    code=code,
                    code_verifier=transaction.pkce_verifier,
                    include_client_id=True,
                    client_secret=flow.client_config["client_secret"],
                )
                scope_alias_normalized = False
            except Warning as warning:
                token = _recover_equivalent_scope_warning(warning, expected_scopes)
                if token is None:
                    _log_stage_failure("token_exchange", warning)
                    raise GoogleConnectionError("oauth_callback_failed") from warning
                flow.oauth2session.token = token
                scope_alias_normalized = True
            except Exception as exc:
                _log_stage_failure("token_exchange", exc)
                raise GoogleConnectionError("oauth_callback_failed") from exc
            _LOGGER.info(
                "token_exchange_result=PASS scope_alias_normalized=%s",
                "YES" if scope_alias_normalized else "NO",
            )
            credentials = flow.credentials
            _log_token_presence(credentials)
            if not _log_scope_validation(credentials, expected_scopes):
                raise GoogleConnectionError("oauth_callback_failed")
            _LOGGER.info("identity_validation_started=YES")
            try:
                email = self._broker._identity_fetcher(credentials)  # backend-only collaborator
            except Exception as exc:
                _log_stage_failure("identity_validation", exc)
                raise GoogleConnectionError("oauth_callback_failed") from exc
            _LOGGER.info("identity_validation_result=PASS")
            _LOGGER.info("email_claim_present=%s", "YES" if email else "NO")
            _LOGGER.info("email_verified=UNKNOWN")
            if "gmail" in transaction.requested_capabilities and current is not None and email != current.email:
                _LOGGER.info("identity_validation_result=FAIL identity_error_class=identity_mismatch")
                raise GoogleConnectionError("oauth_identity_mismatch")
            _LOGGER.info("dpapi_save_started=YES")
            try:
                granted_scopes = tuple(
                    scope for capability in transaction.requested_capabilities
                    for scope in CAPABILITY_SCOPES[capability]
                )
                self._broker.store_credentials(credentials, email, granted_scopes)
            except Exception as exc:
                _log_stage_failure("dpapi_save", exc)
                raise GoogleConnectionError("oauth_callback_failed") from exc
            _LOGGER.info("dpapi_save_result=PASS")
            try:
                view = self._broker.status()
                record = self._broker._store.load()
                profile = self._broker.lookup(record.account_ref, CALENDAR_PROVIDER_REF) if record else None
            except Exception as exc:
                _log_stage_failure("dpapi_readback", exc)
                raise GoogleConnectionError("oauth_callback_failed") from exc
            _LOGGER.info("dpapi_readback_result=PASS")
            _LOGGER.info("credential_profile_build=%s", "PASS" if profile is not None else "FAIL")
            _LOGGER.info("provider_ref_match=%s", "PASS" if profile is not None else "FAIL")
            _LOGGER.info("google_connection_created=%s", "YES" if view.connected else "NO")
            _LOGGER.info("connected_state_after_save=%s", "YES" if view.connected else "NO")
            return view
        except GoogleConnectionError:
            raise
        except Exception as exc:
            _log_stage_failure("post_consume", exc)
            _LOGGER.info("dpapi_save_result=FAIL")
            raise GoogleConnectionError("oauth_callback_failed") from exc

    def refresh(self, record: GoogleCredentialRecord) -> GoogleCredentialRecord:
        self._require_client_secret()
        if not record.refresh_token:
            raise GoogleConnectionError("refresh_token_missing")
        try:
            credentials = Credentials(
                token=record.access_token, refresh_token=record.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._client_id(), client_secret=self._client_secret(),
                scopes=list(record.scopes),
            )
            credentials.refresh(Request())
            expiry = credentials.expiry.timestamp() if credentials.expiry else None
            return GoogleCredentialRecord(
                record.account_ref, record.email, credentials.token or "",
                credentials.refresh_token or record.refresh_token, expiry, record.scopes,
            )
        except Exception as exc:
            raise GoogleConnectionError("oauth_refresh_failed") from exc

    def _new_flow(self, state: Optional[str] = None, scopes=None) -> Flow:
        flow = Flow.from_client_secrets_file(
            str(self._config.client_secret_file), scopes=list(scopes or ["openid", "email", CALENDAR_SCOPE]),
            state=state,
        )
        flow.redirect_uri = self._config.redirect_uri
        return flow

    def _require_client_secret(self) -> None:
        if not self._config.client_secret_file.is_file():
            raise GoogleConnectionError("oauth_provider_not_configured")

    def _client_id(self) -> str:
        return self._client_data()["client_id"]

    def _client_secret(self) -> str:
        return self._client_data()["client_secret"]

    def _client_data(self) -> dict:
        import json
        try:
            value = json.loads(self._config.client_secret_file.read_text(encoding="utf-8"))
            return value.get("installed") or value["web"]
        except Exception as exc:
            raise GoogleConnectionError("oauth_client_secret_invalid") from exc


def fetch_google_identity(credentials) -> str:
    """Fetch only the verified Google email on the backend."""
    from googleapiclient.discovery import build
    try:
        service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
        identity = service.userinfo().get().execute()
        email = identity.get("email") if isinstance(identity, dict) else None
        if not isinstance(email, str) or not email:
            raise GoogleConnectionError("identity_missing")
        return email
    except GoogleConnectionError:
        raise
    except Exception as exc:
        raise GoogleConnectionError("identity_verification_failed") from exc


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _digest_prefix(value: str) -> str:
    """Return a non-reversible diagnostic identifier; never log OAuth state."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _log_stage_failure(stage: str, exc: Exception) -> None:
    """Log only a typed class name and optional numeric HTTP status."""
    status = getattr(getattr(exc, "response", None), "status", None)
    if not isinstance(status, int):
        status = "UNKNOWN"
    _LOGGER.info(
        "%s_result=FAIL %s_error_class=%s %s_http_status=%s",
        stage, stage, type(exc).__name__, stage, status,
    )


def _log_token_presence(credentials) -> None:
    _LOGGER.info(
        "access_token_present=%s refresh_token_present=%s id_token_present=%s",
        "YES" if getattr(credentials, "token", None) else "NO",
        "YES" if getattr(credentials, "refresh_token", None) else "NO",
        "YES" if getattr(credentials, "id_token", None) else "NO",
    )


def _log_scope_validation(credentials, required_scopes=None) -> bool:
    scopes = getattr(credentials, "scopes", None)
    if scopes is None:
        _LOGGER.info(
            "granted_scopes_validation=PASS calendar_readonly_present=YES "
            "unexpected_scope_present=NO"
        )
        return True
    required = set(required_scopes or _CANONICAL_REQUIRED_SCOPES)
    granted = _canonical_scopes(scopes)
    unexpected = granted - required
    calendar_present = "calendar.readonly" in granted
    identity_present = _IDENTITY_EMAIL_SCOPE in granted
    required_present = required.issubset(granted)
    _LOGGER.info(
        "granted_scopes_validation=%s calendar_readonly_present=%s "
        "identity_email_present=%s unexpected_scope_present=%s",
        "PASS" if required_present and not unexpected else "FAIL",
        "YES" if calendar_present else "NO",
        "YES" if identity_present else "NO",
        "YES" if unexpected else "NO",
    )
    return required_present and not unexpected
