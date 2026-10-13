"""IAOS_SKILL_WITH_REAL_CONTEXT_V1 / IAOS_GOVERNED_MULTI_FILE_CONTEXT_V1: up to 5 files + optional Skill + task, each file read ONLY through the existing governance
pipeline (fs.read authorization, bound handle). Fake model transport; real filesystem under tmp_path; no network.
KNOWN LIMITS: lint-style checks catch accidental violations only; the fs.read executor has no size limit (we refuse to
forward oversized content); evidence is in-memory per process; certified scope is Windows/NTFS.
"""
import ast
import hashlib
import http.client
import json
import os
import pathlib
import threading

import pytest

from governance.aep_executors import default_registry
from skilleval import governed_read as G
from skilleval import manual_runtime as M
from skilleval import manual_server as B
from skilleval import raw_smoke as RS
from skills.library import load_library

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEY = "sk-test-SENTINEL-0123456789"
CODE = "export function pay(q: string) { return db.run('UPDATE a SET b=' + q); }\n"


@pytest.fixture()
def ws(tmp_path):
    base = pathlib.Path(os.path.realpath(tmp_path))
    root = base / "ws"
    root.mkdir()
    (root / "payment.ts").write_bytes(CODE.encode("utf-8"))
    (root / "sub").mkdir()
    (root / "sub" / "inner.py").write_bytes("print('inner')\n".encode("utf-8"))
    (root / "binary.bin").write_bytes(b"\xff\xfe\x00\x01bad")
    (root / "big.txt").write_bytes(("x" * 500).encode("utf-8"))
    (base / "outside.txt").write_bytes("TOP-SECRET-OUTSIDE".encode("utf-8"))
    (base / "ws_evil").mkdir()  # shares the root as a string prefix
    (base / "ws_evil" / "secret.txt").write_bytes("TOP-SECRET-EVIL".encode("utf-8"))
    return base, root


def snapshot(base):
    return sorted((str(p.relative_to(base)), p.stat().st_size, p.stat().st_mtime_ns) for p in base.rglob("*"))


# ---- governed reader -------------------------------------------------------------------------------------------------

def test_authorized_read_returns_text_through_the_bound_handle(ws):
    base, root = ws
    r = G.GovernedFileReader(str(root))
    gf = r.read("payment.ts")
    assert gf.content == CODE and gf.chars == len(CODE) and gf.sha256 == hashlib.sha256(CODE.encode()).hexdigest()
    assert gf.bound_handle_reads == 1 and gf.path_open_calls_during_exec == 0  # BOUND_RESOURCE_USED
    log = r.effect_log()
    assert len(log) == 1 and log[0]["opcode"] == "FS_READ" and log[0]["resource"].endswith("payment.ts")
    assert [c[0] for c in r.runtime.calls] == ["BOOT", "EXEC", "HALT"]
    kinds = [e["kind"] for e in r.store.events()]
    assert "AUTHORIZATION" in kinds and "EXECUTION_RESULT" in kinds
    assert next(e for e in r.store.events() if e["kind"] == "AUTHORIZATION")["final_decision"] == "ALLOW"
    assert "export function pay" not in json.dumps(r.store.events())  # evidence keeps the digest, never the content
    assert r.bindings.pending() == 0


def test_a_file_in_a_subfolder_of_the_workspace_is_allowed(ws):
    _, root = ws
    assert G.GovernedFileReader(str(root)).read("sub/inner.py").content == "print('inner')\n"


@pytest.mark.parametrize("raw", ["../outside.txt", "OUTSIDE_ABS", "../ws_evil/secret.txt", "sub/../../outside.txt"])
def test_unauthorized_reads_are_rejected_and_nothing_is_read(ws, raw):
    base, root = ws
    raw = str(base / "outside.txt") if raw == "OUTSIDE_ABS" else raw
    r = G.GovernedFileReader(str(root))
    with pytest.raises(G.ReadDenied) as e:
        r.read(raw)
    assert "resource_outside_root" in e.value.reason
    assert r.effect_log() == [] and r.bindings.read_log() == () and r.bindings.pending() == 0
    assert r.runtime.calls == []  # never reached the effect boundary
    assert "TOP-SECRET" not in e.value.reason


def test_missing_file_and_directory_are_rejected(ws):
    _, root = ws
    r = G.GovernedFileReader(str(root))
    with pytest.raises(G.ReadDenied) as e:
        r.read("nope.ts")
    assert "resource_not_found" in e.value.reason
    with pytest.raises(G.ReadDenied):
        r.read("sub")
    assert r.effect_log() == []


@pytest.mark.parametrize("bad", ["", "   ", "x" * 2000, 5, None, "a\x00b"])
def test_invalid_path_inputs_are_rejected(ws, bad):
    _, root = ws
    r = G.GovernedFileReader(str(root))
    with pytest.raises(G.ReadDenied):
        r.read(bad)
    assert r.effect_log() == []


def test_binary_and_oversized_files_do_not_become_context(ws):
    _, root = ws
    with pytest.raises(G.ReadFailed):
        G.GovernedFileReader(str(root)).read("binary.bin")
    with pytest.raises(G.FileTooLarge):
        G.GovernedFileReader(str(root), max_chars=100).read("big.txt")


def test_no_write_effect_whatever_happens(ws):
    base, root = ws
    before = snapshot(base)
    r = G.GovernedFileReader(str(root))
    r.read("payment.ts")
    for raw in ("../outside.txt", "nope.ts", "sub", "binary.bin"):
        with pytest.raises(Exception):
            r.read(raw)
    assert snapshot(base) == before
    assert default_registry().actions() == ("fs.read", "fs.read_bytes")  # the file-context feature itself only ever proposes fs.read
    assert {e["opcode"] for e in r.effect_log()} <= {"FS_READ"}


# ---- prompt composition ----------------------------------------------------------------------------------------------

def test_prompt_without_file_is_unchanged_and_with_file_orders_skill_file_task():
    raw = load_library().get("code-reviewer").raw
    assert M.build_context_prompt("t") == RS.build_prompt("t")
    assert M.build_context_prompt("t", raw) == RS.build_prompt("t", raw)
    f = (M.ContextFile("payment.ts", CODE),)
    p = M.build_context_prompt("revise", raw, f)
    assert p.index(raw.text) < p.index(M.FILE_LEAD) < p.index(CODE) < p.index("=== TASK ===\nrevise") and p.endswith("revise")
    q = M.build_context_prompt("revise", None, f)
    assert q.startswith(M.FILE_LEAD) and CODE in q and raw.text not in q and q.endswith("revise")


# ---- bridge: governed file flow --------------------------------------------------------------------------------------

class Fake:
    def __init__(self):
        self.payloads = []

    def __call__(self, payload, key):
        self.payloads.append(payload)
        return 200, {"X-Request-Id": "rid"}, {"id": "r", "model": "agnes-2.5-flash", "usage": {"prompt_tokens": 9},
                                              "choices": [{"message": {"content": "resposta"}, "finish_reason": "stop"}]}


@pytest.fixture()
def bridge(ws, monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    _, root = ws
    made = []

    def make(reader="default", key=KEY, max_chars=G.MAX_FILE_CHARS):
        rd = G.GovernedFileReader(str(root), max_chars) if reader == "default" else reader
        t = Fake()
        srv = B.Bridge(("127.0.0.1", 0), load_library(), t, key=key, reader=rd)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append(srv)
        return srv, t, rd

    yield make
    for s in made:
        s.shutdown()
        s.server_close()


def call(srv, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Content-Type": "application/json"} if method == "POST" else {})
    r = c.getresponse()
    return r.status, json.loads(r.read())


def test_file_plus_skill_reach_cognition_through_governance(bridge):
    srv, t, rd = bridge()
    st, out = call(srv, "POST", "/run", {"task": "revise", "skill_id": "code-reviewer", "files": ["payment.ts"]})
    assert st == 200 and out["response"] == "resposta"
    prompt = t.payloads[0]["messages"][0]["content"]
    raw = load_library().get("code-reviewer").raw
    assert raw.text in prompt and CODE in prompt and prompt.endswith("revise")
    assert not ({"tools", "functions", "tool_choice"} & set(t.payloads[0]))
    f = out["files"][0]
    assert f["path"] == "payment.ts" and f["governed_action"] == "fs.read" and f["bound_handle_reads"] == 1
    assert f["path_open_calls_during_exec"] == 0 and f["sha256"] == hashlib.sha256(CODE.encode()).hexdigest()
    assert out["context_files"][0]["chars"] == len(CODE) and KEY not in json.dumps(out)
    assert len(rd.effect_log()) == 1


def test_file_without_skill_and_skill_without_file(bridge):
    srv, t, _ = bridge()
    assert call(srv, "POST", "/run", {"task": "x", "skill_id": None, "files": ["payment.ts"]})[0] == 200
    assert t.payloads[0]["messages"][0]["content"].startswith(M.FILE_LEAD)
    st, out = call(srv, "POST", "/run", {"task": "x", "skill_id": "code-reviewer"})
    assert st == 200 and out["files"] == [] and CODE not in t.payloads[1]["messages"][0]["content"]


@pytest.mark.parametrize("raw", ["../outside.txt", "../ws_evil/secret.txt"])
def test_unauthorized_file_is_403_and_the_model_is_never_called(bridge, ws, raw):
    base, _ = ws
    srv, t, rd = bridge()
    st, out = call(srv, "POST", "/run", {"task": "x", "files": [raw]})
    assert st == 403 and out["error"] == "read_denied" and "resource_outside_root" in out["detail"]
    assert t.payloads == [] and rd.effect_log() == [] and "TOP-SECRET" not in json.dumps(out)
    assert call(srv, "POST", "/run", {"task": "x", "files": [str(base / "outside.txt")]})[0] == 403


def test_missing_oversized_and_binary_files_fail_before_the_model(bridge):
    srv, t, _ = bridge()
    assert call(srv, "POST", "/run", {"task": "x", "files": ["nope.ts"]})[0] == 404
    assert call(srv, "POST", "/run", {"task": "x", "files": ["binary.bin"]})[0] == 422
    srv2, t2, _ = bridge(max_chars=100)
    assert call(srv2, "POST", "/run", {"task": "x", "files": ["big.txt"]})[0] == 413
    assert t.payloads == [] and t2.payloads == []


def test_unknown_skill_or_missing_credential_means_no_file_open_at_all(bridge):
    srv, t, rd = bridge()
    assert call(srv, "POST", "/run", {"task": "x", "skill_id": "nope", "files": ["payment.ts"]})[0] == 404
    srv2, t2, rd2 = bridge(key=None)
    assert call(srv2, "POST", "/run", {"task": "x", "files": ["payment.ts"]})[0] == 503
    assert rd.bindings.open_calls() == 0 and rd2.bindings.open_calls() == 0 and t.payloads == [] and t2.payloads == []


def test_workspace_endpoint_and_disabled_file_context(bridge, ws):
    _, root = ws
    srv, _, rd = bridge()
    assert call(srv, "GET", "/workspace") == (200, {"root": rd.root})
    srv2, t2, _ = bridge(reader=None)
    assert call(srv2, "GET", "/workspace") == (200, {"root": None})
    st, out = call(srv2, "POST", "/run", {"task": "x", "files": ["payment.ts"]})
    assert st == 503 and out["error"] == "workspace_not_configured" and t2.payloads == []


@pytest.mark.parametrize("bad", [[""], ["  "], [5], "a", 5, ["a", "a"], ["a"] * 6, [["a"]]])
def test_invalid_files_field_is_400(bridge, bad):
    srv, t, _ = bridge()
    assert call(srv, "POST", "/run", {"task": "x", "files": bad})[0] == 400 and t.payloads == []


# ---- multi-file ------------------------------------------------------------------------------------------------------

def test_three_files_are_each_authorized_and_all_reach_cognition(bridge):
    srv, t, rd = bridge()
    st, out = call(srv, "POST", "/run", {"task": "revise o fluxo", "skill_id": "code-reviewer",
                                         "files": ["payment.ts", "sub/inner.py", "big.txt"]})
    assert st == 200
    prompt = t.payloads[0]["messages"][0]["content"]
    raw = load_library().get("code-reviewer").raw
    assert raw.text in prompt and CODE in prompt and "print('inner')" in prompt and "x" * 500 in prompt
    assert prompt.index("=== FILE: payment.ts") < prompt.index("=== FILE: sub/inner.py") < prompt.index("=== FILE: big.txt")
    assert prompt.index("=== FILE: big.txt") < prompt.index("=== TASK ===\nrevise o fluxo") and prompt.endswith("revise o fluxo")
    assert [f["path"] for f in out["files"]] == ["payment.ts", "sub/inner.py", "big.txt"]
    assert all(f["governed_action"] == "fs.read" and f["bound_handle_reads"] == 1 and f["path_open_calls_during_exec"] == 0
               for f in out["files"])
    assert len({f["authorization_event_id"] for f in out["files"]}) == 3 and len({f["execution_id"] for f in out["files"]}) == 3
    assert len(rd.effect_log()) == 3 and [c["name"] for c in out["context_files"]] == ["payment.ts", "sub/inner.py", "big.txt"]
    assert not ({"tools", "functions", "tool_choice"} & set(t.payloads[0])) and KEY not in json.dumps(out)


def test_one_rejected_file_aborts_the_whole_request_before_the_model(bridge, ws):
    srv, t, rd = bridge()
    st, out = call(srv, "POST", "/run", {"task": "x", "files": ["payment.ts", "sub/inner.py", "../outside.txt", "big.txt"]})
    assert st == 403 and out["error"] == "read_denied" and out["index"] == 2 and out["path"] == "../outside.txt"
    assert t.payloads == [] and "TOP-SECRET" not in json.dumps(out)
    assert len(rd.effect_log()) == 2  # the two authorized reads happened; the 4th file was never attempted
    st, out = call(srv, "POST", "/run", {"task": "x", "files": ["payment.ts", "nope.ts"]})
    assert st == 404 and out["index"] == 1 and t.payloads == []
    st, out = call(srv, "POST", "/run", {"task": "x", "files": ["payment.ts", "binary.bin"]})
    assert st == 422 and out["index"] == 1 and t.payloads == []


def test_aggregate_limit_applies_to_the_sum_not_to_each_file(bridge, monkeypatch):
    srv, t, _ = bridge()
    monkeypatch.setattr(M, "MODEL_CONTEXT_AGGREGATE_LIMIT", 560)
    assert call(srv, "POST", "/run", {"task": "x", "files": ["big.txt"]})[0] == 200
    st, out = call(srv, "POST", "/run", {"task": "x", "files": ["payment.ts", "big.txt"]})
    assert st == 413 and out["error"] == "context_too_large" and out["index"] == 1 and len(t.payloads) == 1


def test_five_files_ok_six_rejected_and_duplicates_rejected(bridge):
    srv, t, rd = bridge()
    five = ["payment.ts", "sub/inner.py", "big.txt", "sub/../payment.ts", "./sub/inner.py"]
    assert call(srv, "POST", "/run", {"task": "x", "files": five})[0] == 200
    assert call(srv, "POST", "/run", {"task": "x", "files": five + ["payment.ts"]})[0] == 400
    assert len(t.payloads) == 1 and len(rd.effect_log()) == 5


def test_no_write_effect_across_a_multi_file_request(bridge, ws):
    base, _ = ws
    before = snapshot(base)
    srv, _, rd = bridge()
    assert call(srv, "POST", "/run", {"task": "x", "files": ["payment.ts", "sub/inner.py"]})[0] == 200
    assert snapshot(base) == before and {e["opcode"] for e in rd.effect_log()} <= {"FS_READ"}


# ---- structural lint -------------------------------------------------------------------------------------------------

def test_governed_read_has_no_filesystem_write_command_or_network_surface():
    f = ROOT / "skilleval/governed_read.py"
    text = f.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imports = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.level == 0:
            imports.add((n.module or "").split(".")[0])
    assert not (imports & {"subprocess", "ctypes", "msvcrt", "socket", "urllib", "http", "os", "shutil", "pathlib", "importlib"})
    calls = {c.func.id for c in ast.walk(tree) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert not (calls & {"open", "eval", "exec", "compile", "__import__", "AuthorizedAction", "ExecutionEnvelope"})
    assert "fs.write" not in text and "shell" not in text.replace("shell-", "")
    assert text.count('"fs.read"') == 1
