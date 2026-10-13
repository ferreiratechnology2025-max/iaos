"""One-shot, non-persistent Google Calendar credential provisioning.

This module provisions identity and an access token only. It never refreshes,
persists, executes Calendar, or places secrets in product authority objects.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from google_auth_oauthlib.flow import InstalledAppFlow

from governance.aep_executors import CredentialProfile


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
IDENTITY_SCOPES = ("openid", "email")
SCOPES = (CALENDAR_SCOPE,) + IDENTITY_SCOPES
PROVIDER_REF = "google.calendar.v3"


class CalendarProvisioningError(Exception):
    """Secret-free provisioning failure."""


@dataclass(frozen=True)
class ProvisioningConfig:
    client_secret_file: Path
    account_ref: str
    expected_account: str

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> "ProvisioningConfig":
        path = environ.get("GOOGLE_CLIENT_SECRET_FILE")
        account_ref = environ.get("GOOGLE_CALENDAR_ACCOUNT_REF")
        expected = environ.get("GOOGLE_CALENDAR_EXPECTED_ACCOUNT")
        if not path:
            raise CalendarProvisioningError("client_secret_file_missing")
        if not account_ref:
            raise CalendarProvisioningError("account_ref_missing")
        if not expected:
            raise CalendarProvisioningError("expected_account_missing")
        return cls(Path(path), account_ref, expected)


class GoogleCalendarProvisioner:
    """Interactive one-shot OAuth; all external collaborators are injectable."""

    def __init__(
        self,
        flow_factory: Callable[..., Any] = InstalledAppFlow.from_client_secrets_file,
        identity_fetcher: Optional[Callable[[Any], str]] = None,
        local_server_port: int = 0,
    ) -> None:
        self._flow_factory = flow_factory
        self._identity_fetcher = identity_fetcher or self._fetch_identity
        self._local_server_port = local_server_port

    def provision(self, config: ProvisioningConfig) -> CredentialProfile:
        if not config.client_secret_file.is_file():
            raise CalendarProvisioningError("client_secret_file_missing")
        try:
            flow = self._flow_factory(str(config.client_secret_file), scopes=list(SCOPES))
            credentials = flow.run_local_server(
                port=self._local_server_port,
                access_type="offline",
                prompt="consent",
            )
        except CalendarProvisioningError:
            raise
        except Exception as exc:
            raise CalendarProvisioningError("oauth_interactive_failed") from exc
        access_token = getattr(credentials, "token", None)
        if not isinstance(access_token, str) or not access_token:
            raise CalendarProvisioningError("access_token_missing")
        try:
            authenticated_account = self._identity_fetcher(credentials)
        except CalendarProvisioningError:
            raise
        except Exception as exc:
            raise CalendarProvisioningError("identity_verification_failed") from exc
        if authenticated_account != config.expected_account:
            raise CalendarProvisioningError("account_identity_mismatch")
        expiry = getattr(credentials, "expiry", None)
        expires_at = expiry.timestamp() if expiry is not None else None
        # Deliberately copy only the short-lived access token. Refresh material
        # is neither stored in the profile nor returned to the executor.
        return CredentialProfile(
            provider_ref=PROVIDER_REF,
            account_ref=config.account_ref,
            provider_account_ref=authenticated_account,
            access_token=access_token,
            expires_at=expires_at,
        )

    @staticmethod
    def _fetch_identity(credentials: Any) -> str:
        from googleapiclient.discovery import build

        service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
        identity = service.userinfo().get().execute()
        email = identity.get("email") if isinstance(identity, dict) else None
        if not isinstance(email, str) or not email:
            raise CalendarProvisioningError("account_identity_missing")
        return email
