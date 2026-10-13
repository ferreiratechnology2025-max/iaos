from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

from google_auth_oauthlib.flow import Flow

from application.google_connection import CALENDAR_SCOPE
from application.google_oauth_service import GoogleOAuthService, OAuthConfig


class _TokenResponse:
    status_code = 200
    text = json.dumps({"access_token": "access", "token_type": "Bearer", "expires_in": 3600})
    content = text.encode()
    headers = {"content-type": "application/json"}

    def __init__(self, request):
        self.request = request


class _RefreshResponse:
    status = 200
    data = json.dumps({"access_token": "access", "token_type": "Bearer", "expires_in": 3600}).encode()


def _client_file(tmp_path: Path) -> Path:
    path = tmp_path / "client.json"
    path.write_text(json.dumps({
        "installed": {
            "client_id": "iaos-client-id.apps.googleusercontent.com",
            "client_secret": "iaos-client-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://127.0.0.1:8766/integrations/google/callback"],
        }
    }), encoding="utf-8")
    return path


def test_google_token_exchange_uses_form_body_client_auth(tmp_path):
    client = _client_file(tmp_path)
    redirect_uri = "http://127.0.0.1:8766/integrations/google/callback"
    flow = Flow.from_client_secrets_file(
        str(client), scopes=["openid", "email", CALENDAR_SCOPE], state="diagnostic-state",
    )
    flow.redirect_uri = redirect_uri
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        request = type("Request", (), {
            "url": kwargs["url"], "headers": kwargs.get("headers"),
            "body": kwargs.get("data"),
        })()
        return _TokenResponse(request)

    flow.oauth2session.request = fake_request
    flow.fetch_token(
        code="authorization-code",
        code_verifier="pkce-verifier",
        include_client_id=True,
        client_secret="iaos-client-secret",
    )
    body = dict(captured["data"])

    assert captured["url"] == "https://oauth2.googleapis.com/token"
    assert captured["auth"] is None
    assert body == {
        "client_id": "iaos-client-id.apps.googleusercontent.com",
        "client_secret": "iaos-client-secret",
        "code": "authorization-code",
        "code_verifier": "pkce-verifier",
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }


def test_refresh_path_constructs_form_body_client_auth(monkeypatch, tmp_path):
    import application.google_oauth_service as oauth_module
    from application.google_connection import GoogleCredentialRecord

    client = _client_file(tmp_path)
    captured = {}

    class _Request:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return _RefreshResponse()

    monkeypatch.setattr(oauth_module, "Request", lambda: _Request())
    service = GoogleOAuthService.__new__(GoogleOAuthService)
    service._config = OAuthConfig(client, "http://127.0.0.1:8766/integrations/google/callback")
    record = GoogleCredentialRecord(
        "internal", "person@example.com", "access", "refresh-token", None,
        ("openid", "email", CALENDAR_SCOPE),
    )
    refreshed = service.refresh(record)
    body = {key: values[-1] for key, values in parse_qs(captured["body"].decode()).items()}

    assert refreshed.access_token == "access"
    assert captured["url"] == "https://oauth2.googleapis.com/token"
    assert body["client_id"] == "iaos-client-id.apps.googleusercontent.com"
    assert body["client_secret"] == "iaos-client-secret"
    assert body["refresh_token"] == "refresh-token"
    assert body["grant_type"] == "refresh_token"
    assert "Authorization" not in captured["headers"]
