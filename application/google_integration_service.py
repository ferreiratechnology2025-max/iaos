"""Start the loopback Google integration backend.

The client secret is service configuration, never an end-user setup step.
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

from application.google_connection import GoogleCredentialBroker
from application.google_integration_server import GoogleIntegrationServer
from application.google_oauth_service import GoogleOAuthService, OAuthConfig, fetch_google_identity
from application.google_secure_store import WindowsDPAPIGoogleCredentialStore
from application.oauth_transaction_store import WindowsDPAPIOAuthTransactionStore
from application.daily_brief import DailyBriefSynthesizer, DailyBriefWorkflow, configured_agnes_provider
from application.daily_brief_api import DailyBriefApi
from application.daily_brief_history import JsonDailyBriefHistoryStore
from application.daily_brief_routine import DailyBriefRoutineService, JsonDailyBriefRoutineStore
from application.scouts import ScoutService, JsonScoutStore, JsonScoutHistoryStore


def build_server() -> GoogleIntegrationServer:
    client_secret = os.environ.get("IAOS_GOOGLE_CLIENT_SECRET_FILE", "")
    store = WindowsDPAPIGoogleCredentialStore()
    broker = GoogleCredentialBroker(
        client_secret, store, flow_factory=lambda *_args, **_kwargs: None,
        identity_fetcher=fetch_google_identity,
    )
    oauth = GoogleOAuthService(OAuthConfig(
        client_secret_file=Path(client_secret),
        redirect_uri=os.environ.get("IAOS_GOOGLE_REDIRECT_URI", "http://127.0.0.1:8766/integrations/google/callback"),
    ), broker, transaction_store=WindowsDPAPIOAuthTransactionStore())
    broker.set_refresh_adapter(oauth.refresh)
    scouts = ScoutService(JsonScoutStore(), JsonScoutHistoryStore())
    daily_brief = DailyBriefApi(DailyBriefWorkflow(
        broker,
        synthesizer=DailyBriefSynthesizer(configured_agnes_provider()),
        web_allowed_domains=("en.wikipedia.org",),
        scout_findings_provider=scouts.unconsumed_findings,
    ), JsonDailyBriefHistoryStore(), scout_service=scouts)
    routine = DailyBriefRoutineService(daily_brief, JsonDailyBriefRoutineStore())
    routine.start()
    scouts.start()
    return GoogleIntegrationServer(
        ("127.0.0.1", 8766), broker, oauth,
        ui_origin=os.environ.get("IAOS_GOOGLE_UI_ORIGIN", "http://127.0.0.1:5173"),
        daily_brief=daily_brief,
        routine=routine,
        scouts=scouts,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = build_server()
    print("IAOS Google integration on http://127.0.0.1:8766")
    server.serve_forever()


if __name__ == "__main__":
    main()
