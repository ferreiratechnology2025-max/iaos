"""Small IAOS-side boundary for the human computer panel.

The browser only sees typed control endpoints and a same-origin WebSocket. The
OpenBot token and computer URL are loaded by this process and never enter the
browser-facing response, URL, or log output.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from application.computer_runtime import ComputerRuntimeError, OpenBotComputerAdapter


LOG = logging.getLogger(__name__)


_CLIENT_GONE = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)
# Winsock/POSIX codes for "this socket is already closed": EBADF, ENOTCONN, ESHUTDOWN, WSAENOTSOCK, WSAESHUTDOWN
_CLOSED_SOCKET_ERRNOS = {9, 107, 108, 10038, 10057, 10058}


def _is_expected_disconnect(exc: BaseException) -> bool:
    """A peer reset/abort or an already-closed socket: normal for a browser reload or a superseded stream."""
    if isinstance(exc, _CLIENT_GONE):
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in _CLOSED_SOCKET_ERRNOS


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class ComputerRuntimeServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, adapter: OpenBotComputerAdapter):
        super().__init__(address, ComputerRuntimeHandler)
        self.adapter = adapter
        self._stream_lock = threading.Lock()
        self._active_stream: socket.socket | None = None

    def claim_stream(self, connection: socket.socket) -> None:
        with self._stream_lock:
            previous = self._active_stream
            self._active_stream = connection
        if previous is not None and previous is not connection:
            try:
                previous.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                previous.close()
            except OSError:
                pass

    def release_stream(self, connection: socket.socket) -> None:
        with self._stream_lock:
            if self._active_stream is connection:
                self._active_stream = None


class ComputerRuntimeHandler(BaseHTTPRequestHandler):
    server: ComputerRuntimeServer
    server_version = "IAOSComputerRuntime/1"

    def handle(self) -> None:
        # A browser closing a keep-alive connection while we wait for its next request is routine.
        try:
            super().handle()
        except OSError as exc:
            if not _is_expected_disconnect(exc):
                raise
            self.close_connection = True

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/computer/stream":
            # Never routed through the HTTP error handler below: after the upgrade there is no HTTP.
            self._stream()
            return
        try:
            if path == "/computer/control":
                self._json(200, self.server.adapter.read_control())
            else:
                self._json(404, {"errorCode": "not_found"})
        except ComputerRuntimeError as exc:
            self._json(502, {"errorCode": str(exc)})
        except Exception:
            self._json(502, {"errorCode": "computer_runtime_unavailable"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/computer/take":
                # On demand: the adapter reuses a waiting request or creates one upstream.
                # Any client-supplied requestId is ignored; upstream state decides.
                self._json(200, self.server.adapter.take_control_on_demand())
            elif path == "/computer/release":
                self._json(200, self.server.adapter.release_control(body.get("requestId", "")))
            else:
                self._json(404, {"errorCode": "not_found"})
        except ComputerRuntimeError as exc:
            self._json(409, {"errorCode": str(exc)})
        except (ValueError, TypeError):
            self._json(400, {"errorCode": "invalid_request"})
        except Exception:
            self._json(502, {"errorCode": "computer_runtime_unavailable"})

    def log_message(self, format: str, *args: Any) -> None:
        # Never log request headers or query strings: either could contain a secret.
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16 * 1024:
            raise ValueError("invalid_request_body")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid_request_body")
        return value

    def _json(self, status: int, value: Any) -> None:
        payload = _json_bytes(value)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except OSError as exc:
            if not _is_expected_disconnect(exc):
                raise
            # The client left mid-response; there is nobody to answer, and no second write to attempt.
            self.close_connection = True

    def _stream(self) -> None:
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._json(426, {"errorCode": "websocket_required"})
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self._json(400, {"errorCode": "websocket_key_required"})
            return

        # BEFORE the upgrade: a real upstream failure is still an HTTP answer.
        try:
            upstream = _open_upstream(self.server.adapter)
        except ComputerRuntimeError as exc:
            self._json(502, {"errorCode": str(exc)})
            return
        except Exception:
            self._json(502, {"errorCode": "computer_runtime_unavailable"})
            return
        client = self.connection
        self.server.claim_stream(client)
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        # AFTER the upgrade (including the 101 itself) there is no HTTP: a reset, abort, broken pipe
        # or already-closed socket just ends the relay. Nothing is written back to the client.
        try:
            client.sendall(
                ("HTTP/1.1 101 Switching Protocols\r\n"
                 "Upgrade: websocket\r\n"
                 "Connection: Upgrade\r\n"
                 f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()
            )
            _relay(client, upstream)
        except (OSError, ValueError) as exc:
            # ValueError is what select() raises on a socket the other side already closed.
            if not (_is_expected_disconnect(exc) or (isinstance(exc, ValueError) and (client.fileno() < 0 or upstream.fileno() < 0))):
                raise
        finally:
            self.close_connection = True  # the connection is not HTTP any more; do not read another request
            self.server.release_stream(client)
            for connection in (upstream, client):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection.close()
                except OSError:
                    pass


def _open_upstream(adapter: OpenBotComputerAdapter) -> socket.socket:
    parsed = urlparse(adapter._endpoint)  # server-side adapter state only
    if parsed.hostname is None or parsed.port is None:
        raise ComputerRuntimeError("computer_endpoint_requires_host_port")
    connection = socket.create_connection((parsed.hostname, parsed.port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    path = f"/stream?bot={adapter._bot_id}&token={adapter._token}"
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).encode()
    connection.sendall(request)
    response = _read_headers(connection)
    if not response.startswith(b"HTTP/1.1 101"):
        connection.close()
        raise ComputerRuntimeError("computer_stream_rejected")
    return connection


def _read_headers(connection: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data and len(data) <= 64 * 1024:
        chunk = connection.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    client.settimeout(None)
    upstream.settimeout(None)
    while True:
        readable, _, _ = select.select([client, upstream], [], [], 60)
        if not readable:
            continue
        for source in readable:
            data = source.recv(64 * 1024)
            if not data:
                return
            destination = upstream if source is client else client
            destination.sendall(data)


def build_server() -> ComputerRuntimeServer:
    endpoint = os.environ.get("IAOS_COMPUTER_URL", "").strip()
    bot_id = os.environ.get("IAOS_COMPUTER_BOT_ID", "").strip()
    token = os.environ.get("IAOS_COMPUTER_TOKEN", "")
    if not endpoint or not bot_id or not token:
        raise RuntimeError("IAOS_COMPUTER_URL, IAOS_COMPUTER_BOT_ID and IAOS_COMPUTER_TOKEN are required")
    adapter = OpenBotComputerAdapter(endpoint, bot_id, token)
    return ComputerRuntimeServer(("127.0.0.1", int(os.environ.get("IAOS_COMPUTER_RUNTIME_PORT", "8767"))), adapter)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = build_server()
    print("IAOS computer runtime listening on http://127.0.0.1:8767", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
