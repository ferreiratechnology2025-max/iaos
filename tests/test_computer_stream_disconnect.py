import socket
import struct
import threading
import time

from application import computer_runtime_server as srv
from application.computer_runtime import OpenBotComputerAdapter
from application.computer_runtime_server import ComputerRuntimeServer

UPGRADE = (b"GET /computer/stream HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")


class FakeUpstream:
    """Speaks just enough of OpenBot's /stream: 101, then holds the socket open."""
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        self.accepted = 0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.accepted += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _serve(conn):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            while conn.recv(4096):
                pass
        except OSError:
            pass
        finally:
            conn.close()


def _rig(monkeypatch):
    upstream = FakeUpstream()
    server = ComputerRuntimeServer(("127.0.0.1", 0), OpenBotComputerAdapter(f"http://127.0.0.1:{upstream.port}", "bot", "tok"))
    errors, json_calls = [], []
    server.handle_error = lambda request, client_address: errors.append(client_address)  # a traceback would land here
    original = srv.ComputerRuntimeHandler._json
    monkeypatch.setattr(srv.ComputerRuntimeHandler, "_json", lambda self, status, value: (json_calls.append(status), original(self, status, value))[1])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, upstream, errors, json_calls


def _open_stream(port):
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.sendall(UPGRADE)
    head = b""
    while b"\r\n\r\n" not in head:
        head += client.recv(4096)
    return client, head


def _reset(client):
    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # close() sends RST
    client.close()


def test_client_reset_after_upgrade_is_silent_and_a_new_stream_still_works(monkeypatch):
    server, upstream, errors, json_calls = _rig(monkeypatch)
    port = server.server_address[1]
    try:
        client, head = _open_stream(port)
        assert head.startswith(b"HTTP/1.1 101")
        _reset(client)
        time.sleep(0.5)

        assert errors == []                       # no traceback from the handler
        assert not [s for s in json_calls if s >= 500]  # no HTTP 502 written after the upgrade
        assert server._active_stream is None      # the dead stream was released

        again, head = _open_stream(port)          # the runtime is alive and serves a new stream
        assert head.startswith(b"HTTP/1.1 101")
        assert upstream.accepted == 2
        again.close()
    finally:
        server.shutdown()
        server.server_close()


def test_new_stream_supersedes_previous_without_errors(monkeypatch):
    server, upstream, errors, json_calls = _rig(monkeypatch)
    port = server.server_address[1]
    try:
        first, _ = _open_stream(port)
        second, head = _open_stream(port)
        assert head.startswith(b"HTTP/1.1 101")
        time.sleep(0.5)
        assert errors == []
        assert not [s for s in json_calls if s >= 500]
        first.close(); second.close()
    finally:
        server.shutdown()
        server.server_close()