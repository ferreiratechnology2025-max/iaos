"""Loopback bridge for the Skill UI: HTTP contract and guards, with a fake model transport (no network, no real key)."""
import http.client
import json
import threading

import pytest

from skilleval import manual_server as B
from skills.library import load_library

KEY = "sk-test-SENTINEL-0123456789"


class Fake:
    def __init__(self, exc=None):
        self.payloads, self.exc = [], exc

    def __call__(self, payload, key):
        if self.exc:
            raise self.exc
        self.payloads.append(payload)
        return 200, {"X-Request-Id": "rid"}, {"id": "r", "model": "agnes-2.5-flash", "usage": {"total_tokens": 1},
                                              "choices": [{"message": {"content": "ok-resposta"}, "finish_reason": "stop"}]}


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    made = []

    def make(transport=None, key=KEY):
        srv = B.Bridge(("127.0.0.1", 0), load_library(), transport or Fake(), key=key)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append(srv)
        return srv

    yield make
    for s in made:
        s.shutdown()
        s.server_close()


def req(srv, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    h = {"Content-Type": "application/json"} if method == "POST" else {}
    h.update(headers or {})
    c.request(method, path, body=body if isinstance(body, (bytes, type(None))) else json.dumps(body), headers=h)
    r = c.getresponse()
    data = r.read()
    return r.status, json.loads(data) if data else None


def test_listing_returns_the_three_skills(server):
    st, body = req(server(), "GET", "/skills")
    assert st == 200 and {s["id"] for s in body["skills"]} == {"incident-response-commander", "code-reviewer", "backend-architect"}


def test_run_without_and_with_skill(server):
    t = Fake()
    srv = server(t)
    st, a = req(srv, "POST", "/run", {"task": "olá", "skill_id": None})
    assert st == 200 and a["response"] == "ok-resposta" and a["skill_name"] is None and a["tools_exposed"] is False
    st, b = req(srv, "POST", "/run", {"task": "olá", "skill_id": "code-reviewer"})
    assert st == 200 and b["skill_name"] == "Code Reviewer" and b["provider"] == "Agnes AI" and b["returned_model"] == "agnes-2.5-flash"
    assert len(t.payloads) == 2 and not ({"tools", "functions", "tool_choice"} & set(t.payloads[0]))
    assert KEY not in json.dumps(a) + json.dumps(b)


def test_unknown_skill_is_404_and_never_reaches_the_model(server):
    t = Fake()
    st, body = req(server(t), "POST", "/run", {"task": "x", "skill_id": "nope"})
    assert st == 404 and body["error"] == "unknown_skill" and t.payloads == []


@pytest.mark.parametrize("body", [{"task": ""}, {"task": "  "}, {"task": 5}, {"task": "x", "skill_id": 3}, [], {"task": "x" * 20001}])
def test_invalid_bodies_are_400(server, body):
    t = Fake()
    st, out = req(server(t), "POST", "/run", body)
    assert st == 400 and out["error"] == "invalid" and t.payloads == []


def test_not_json_and_wrong_content_type_and_oversize(server):
    srv = server()
    assert req(srv, "POST", "/run", b"{not json")[0] == 400
    assert req(srv, "POST", "/run", {"task": "x"}, {"Content-Type": "text/plain"})[0] == 415
    assert req(srv, "POST", "/run", b"x" * (B.MAX_BODY + 1))[0] == 413


def test_foreign_origin_and_non_loopback_host_are_forbidden(server):
    srv = server()
    assert req(srv, "GET", "/skills", headers={"Origin": "https://evil.example"})[0] == 403
    assert req(srv, "GET", "/skills", headers={"Origin": "http://127.0.0.1:5173"})[0] == 200
    assert req(srv, "GET", "/skills", headers={"Host": "evil.example"})[0] == 403
    assert req(srv, "POST", "/run", {"task": "x"}, {"Origin": "https://evil.example"})[0] == 403


def test_missing_credential_is_503_without_model_call(server):
    t = Fake()
    st, body = req(server(t, key=None), "POST", "/run", {"task": "x"})
    assert st == 503 and body["error"] == "not_configured" and t.payloads == []


def test_upstream_failures_are_502_and_leak_nothing(server):
    import urllib.error
    exc = urllib.error.HTTPError("u", 401, "bad", {}, None)
    st, body = req(server(Fake(exc)), "POST", "/run", {"task": "x"})
    assert st == 502 and body == {"error": "upstream", "detail": "HTTP 401"}
    st, body = req(server(Fake(urllib.error.URLError("down"))), "POST", "/run", {"task": "x"})
    assert st == 502 and body["detail"] == "unreachable"


def test_unknown_paths_are_404_and_binding_is_loopback(server):
    srv = server()
    assert req(srv, "GET", "/nope")[0] == 404 and req(srv, "POST", "/nope", {})[0] == 404
    assert srv.server_address[0] == "127.0.0.1"
