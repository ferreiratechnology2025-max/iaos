"""Loopback HTTP boundary for the Google Settings integration."""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from application.google_connection import GoogleConnectionError, GoogleConnectionView


class GoogleIntegrationServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, broker, oauth, ui_origin="http://127.0.0.1:5173", daily_brief=None, routine=None, scouts=None):
        super().__init__(address, GoogleIntegrationHandler)
        self.broker = broker
        self.oauth = oauth
        self.ui_origin = ui_origin
        self.daily_brief = daily_brief
        self.routine = routine
        self.scouts = scouts
        self.instance_id = secrets.token_hex(8)
        set_server_instance_id = getattr(oauth, "set_server_instance_id", None)
        if set_server_instance_id is not None:
            set_server_instance_id(self.instance_id)


class GoogleIntegrationHandler(BaseHTTPRequestHandler):
    server: GoogleIntegrationServer
    server_version = "IAOSGoogleIntegration/1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/integrations/google":
                self._json(200, _view(self.server.broker.status()))
            elif path == "/integrations/google/callback":
                query = parse_qs(urlparse(self.path).query)
                state = query.get("state", [""])[0]
                code = query.get("code", [""])[0]
                try:
                    self.server.oauth.callback(state, code)
                except GoogleConnectionError as exc:
                    self._redirect_ui(error=_safe_callback_error(str(exc)))
                else:
                    self._redirect_ui()
            elif path == "/daily-brief/history":
                if self.server.daily_brief is None:
                    self._json(503, {"errorCode": "daily_brief_unavailable"})
                    return
                self._json(200, {"runs": self.server.daily_brief.list_history()})
            elif path.startswith("/daily-brief/history/"):
                if self.server.daily_brief is None:
                    self._json(503, {"errorCode": "daily_brief_unavailable"})
                    return
                run_id = path.removeprefix("/daily-brief/history/")
                run = self.server.daily_brief.get_history(run_id)
                if run is None:
                    self._json(404, {"errorCode": "daily_brief_run_not_found"})
                else:
                    self._json(200, run)
            elif path == "/daily-brief/routine":
                if self.server.routine is None:
                    self._json(503, {"errorCode": "daily_brief_routine_unavailable"})
                    return
                self._json(200, _routine_view(self.server.routine.get()))
            elif path == "/scouts":
                if self.server.scouts is None:
                    self._json(503, {"errorCode": "scouts_unavailable"})
                    return
                self._json(200, {"scouts": [item.to_dict() for item in self.server.scouts.list()]})
            elif path == "/scouts/history":
                if self.server.scouts is None:
                    self._json(503, {"errorCode": "scouts_unavailable"})
                    return
                scout_id = parse_qs(urlparse(self.path).query).get("scout_id", [None])[0]
                self._json(200, {"runs": [item.to_dict() for item in self.server.scouts.history(scout_id)]})
            elif path.startswith("/scouts/"):
                self._json(404, {"errorCode": "not_found"})
            else:
                self._json(404, {"errorCode": "not_found"})
        except GoogleConnectionError as exc:
            self._json(400, {"errorCode": str(exc)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/integrations/google/connect":
                self._json(200, {"authorizationUrl": self.server.oauth.begin()})
            elif path == "/integrations/google/disconnect":
                self.server.broker.disconnect()
                self._json(200, _view(self.server.broker.status()))
            elif path == "/integrations/google/capabilities/gmail":
                view = self.server.broker.status()
                if not view.connected or not view.calendar:
                    raise GoogleConnectionError("google_connection_required")
                if view.gmail:
                    self._json(200, _view(view))
                else:
                    self._json(200, {"authorizationUrl": self.server.oauth.begin(("calendar", "gmail"))})
            elif path == "/integrations/google/capabilities/gmail-send":
                view = self.server.broker.status()
                if not view.connected:
                    raise GoogleConnectionError("google_connection_required")
                record = self.server.broker._store.load()
                if record is not None and "https://www.googleapis.com/auth/gmail.send" in record.scopes:
                    self._json(200, {"alreadyGranted": True, "account": record.email})
                else:
                    self._json(200, {"authorizationUrl": self.server.oauth.begin(("gmail_send",))})
            elif path == "/daily-brief/run":
                if self.server.daily_brief is None:
                    self._json(503, {"errorCode": "daily_brief_unavailable"})
                    return
                payload = self._read_json_body()
                self._json(200, self.server.daily_brief.run(payload))
            elif path == "/daily-brief/routine":
                if self.server.routine is None:
                    self._json(503, {"errorCode": "daily_brief_routine_unavailable"})
                    return
                payload = self._read_json_body()
                routine = self.server.routine.configure(
                    payload.get("time"), payload.get("timezone"), payload.get("enabled", False)
                )
                self._json(200, _routine_view(routine))
            elif path == "/scouts":
                if self.server.scouts is None:
                    self._json(503, {"errorCode": "scouts_unavailable"})
                    return
                self._json(200, self.server.scouts.save(self._read_json_body()).to_dict())
            elif path.startswith("/scouts/") and path.endswith("/run"):
                if self.server.scouts is None:
                    self._json(503, {"errorCode": "scouts_unavailable"})
                    return
                scout_id = path[len("/scouts/"):-len("/run")].strip("/")
                self._json(200, self.server.scouts.run(scout_id).to_dict())
            else:
                self._json(404, {"errorCode": "not_found"})
        except GoogleConnectionError as exc:
            self._json(400, {"errorCode": str(exc)})
        except (ValueError, TypeError, KeyError):
            self._json(400, {"errorCode": "scout_invalid_request" if path.startswith("/scouts") else "daily_brief_invalid_request"})
        except Exception:
            self._json(502, {"errorCode": "scout_failed" if path.startswith("/scouts") else "daily_brief_failed"})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            if path.startswith("/scouts/"):
                if self.server.scouts is None:
                    self._json(503, {"errorCode": "scouts_unavailable"})
                    return
                scout_id = path[len("/scouts/"):].strip("/")
                self.server.scouts.delete(scout_id)
                self._json(200, {"deleted": True, "scout_id": scout_id})
            else:
                self._json(404, {"errorCode": "not_found"})
        except KeyError:
            self._json(404, {"errorCode": "scout_not_found"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, status: int, body: Any) -> None:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 64 * 1024:
            raise ValueError("invalid_request_body")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("invalid_request_body")
        return body

    def _redirect_ui(self, error: str | None = None) -> None:
        location = self.server.ui_origin + "/#/settings/integrations/google"
        if error is not None:
            location += "?" + urlencode({"error": error})
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


_SAFE_CALLBACK_ERRORS = frozenset({
    "oauth_state_invalid_or_expired", "oauth_code_missing", "oauth_callback_failed",
    "oauth_transaction_store_unavailable", "oauth_provider_not_configured",
    "oauth_client_secret_invalid",
})


def _safe_callback_error(error_code: str) -> str:
    return error_code if error_code in _SAFE_CALLBACK_ERRORS else "oauth_callback_failed"


def _view(view: GoogleConnectionView) -> dict:
    # Deliberately closed: no account_ref, tokens, scopes or profile object.
    return {"connected": view.connected, "email": view.email, "calendar": view.calendar, "gmail": view.gmail}


def _routine_view(routine) -> dict:
    if routine is None:
        return {"routine": None}
    return {"routine": routine.to_dict()}
