import json
import threading
import urllib.request

import httpx

from application.computer_runtime import OpenBotComputerAdapter
from application.computer_runtime_server import ComputerRuntimeServer

TOKEN = "unit-test-secret-token"


def _upstream(state):
    """Fake OpenBot agent-computer. `state` is mutated like the real control store."""
    calls = []

    def handler(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        assert request.headers["x-openbot-computer-token"] == TOKEN
        path = request.url.path
        if path == "/run":
            return httpx.Response(200, json={"run": "session-1"})
        if path == "/control":
            return httpx.Response(200, json=dict(state))
        if path == "/control/request":
            state.update(requested=True, request={"id": "req-new", "status": "waiting", "reason": body["reason"]})
            return httpx.Response(200, json=dict(state))
        if path == "/control/take":
            assert body == {"requestId": state["request"]["id"]}
            state.update(holder="human", requested=False)
            state["request"] = {**state["request"], "status": "taken"}
            return httpx.Response(200, json=dict(state))
        return httpx.Response(404)

    return calls, httpx.MockTransport(handler)


def _paths(calls):
    return [path for _, path, _ in calls if path != "/run"]


def test_take_with_existing_request_reuses_it():
    state = {"holder": "bot", "requested": True, "request": {"id": "req-existing", "status": "waiting", "reason": "bot asked"}}
    calls, transport = _upstream(state)
    result = OpenBotComputerAdapter("http://computer", "bot-1", TOKEN, transport=transport).take_control_on_demand()
    assert result["holder"] == "human"
    assert _paths(calls) == ["/control", "/control/take"]
    assert [b for _, p, b in calls if p == "/control/take"] == [{"requestId": "req-existing"}]


def test_take_without_request_creates_request_once_then_takes():
    # A stale, cancelled request must not be reused.
    state = {"holder": "bot", "requested": False, "request": {"id": "req-old", "status": "cancelled", "reason": "old"}}
    calls, transport = _upstream(state)
    result = OpenBotComputerAdapter("http://computer", "bot-1", TOKEN, transport=transport).take_control_on_demand()
    assert result["holder"] == "human"
    assert _paths(calls) == ["/control", "/control/request", "/control/take"]
    assert _paths(calls).count("/control/request") == 1
    assert [b for _, p, b in calls if p == "/control/take"] == [{"requestId": "req-new"}]


def test_release_existing_behavior_unchanged():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/run":
            return httpx.Response(200, json={"run": "session-1"})
        return httpx.Response(200, json={"holder": "bot", "requested": False})

    adapter = OpenBotComputerAdapter("http://computer", "bot-1", TOKEN, transport=httpx.MockTransport(handler))
    assert adapter.release_control("req-1")["holder"] == "bot"
    assert [p for p in seen if p != "/run"] == ["/control/release"]


def test_server_take_endpoint_creates_request_server_side_without_leaking_token():
    state = {"holder": "bot", "requested": False}
    calls, transport = _upstream(state)
    adapter = OpenBotComputerAdapter("http://computer", "bot-1", TOKEN, transport=transport)
    server = ComputerRuntimeServer(("127.0.0.1", 0), adapter)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/computer/take", data=b"{}", method="POST",
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            text = response.read().decode()
        assert json.loads(text)["holder"] == "human"
        assert TOKEN not in text
        assert _paths(calls) == ["/control", "/control/request", "/control/take"]
    finally:
        server.shutdown()
        server.server_close()
