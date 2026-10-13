"""IAOS_MANUAL_SKILL_RUNTIME (offline): manual listing/selection of real pinned raw Skills; fake transport, no network.
KNOWN LIMIT: "no authority" is structural (no tools in the request, no authority types, no governance imports).
"""
import ast
import json
import pathlib

import pytest

from skilleval import manual_runtime as M
from skilleval import raw_smoke as S
from skills import library as L
from skills.raw import RawSkillError, git_blob_sha1

ROOT = pathlib.Path(__file__).resolve().parents[1]
SENTINEL_KEY = "sk-test-SENTINEL-0123456789"
IDS = {"incident-response-commander", "code-reviewer", "backend-architect"}


class FakeTransport:
    def __init__(self, tool_calls=None):
        self.payloads, self.keys, self.tool_calls = [], [], tool_calls

    def __call__(self, payload, key):
        self.payloads.append(payload)
        self.keys.append(key)
        msg = {"role": "assistant", "content": "resposta"}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return 200, {"X-Request-Id": "rid-1"}, {"id": "resp-1", "model": "agnes-2.5-flash",
                                                  "choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"total_tokens": 3}}


def test_listing_shows_id_name_source_for_the_three_pinned_skills():
    rows = M.list_skills()
    assert {r["id"] for r in rows} == IDS
    assert all(set(r) == {"id", "name", "source"} and r["source"].startswith("msitarzewski/agency-agents@83294689da38:") for r in rows)


def test_every_entry_is_the_exact_pinned_blob_and_text_is_unmodified():
    lib = L.load_library()
    for sid in IDS:
        raw = lib.get(sid).raw
        assert git_blob_sha1(raw.text.encode("utf-8")) == raw.blob
        assert raw.license == "MIT"


def test_selected_skill_text_reaches_the_request_unchanged_and_without_tools():
    t = FakeTransport()
    out = M.run_task("revise este PR", "code-reviewer", transport=t, key=SENTINEL_KEY)
    raw = L.load_library().get("code-reviewer").raw
    prompt = t.payloads[0]["messages"][0]["content"]
    assert raw.text in prompt and prompt.endswith("revise este PR")
    assert not ({"tools", "functions", "tool_choice", "function_call"} & set(t.payloads[0]))
    assert out["skill_id"] == "code-reviewer" and out["skill_blob"] == raw.blob and out["tools_exposed"] is False
    assert out["response"] == "resposta" and out["request_id"] == "rid-1" and out["returned_model"] == "agnes-2.5-flash"


def test_without_skill_the_prompt_is_the_bare_task():
    t = FakeTransport()
    out = M.run_task("tarefa", None, transport=t, key=SENTINEL_KEY)
    assert t.payloads[0]["messages"][0]["content"] == "tarefa" and out["skill_id"] is None and out["skill_blob"] is None


@pytest.mark.parametrize("bad", ["", "Code-Reviewer", "nope", "code-reviewer ", 5])
def test_unknown_skill_fails_closed_before_any_model_call(bad):
    t = FakeTransport()
    with pytest.raises(L.UnknownSkillError):
        M.run_task("x", bad, transport=t, key=SENTINEL_KEY)
    assert t.payloads == []


def test_empty_task_and_missing_credential_fail_closed(monkeypatch):
    t = FakeTransport()
    with pytest.raises(ValueError):
        M.run_task("  ", None, transport=t, key=SENTINEL_KEY)
    monkeypatch.delenv(S.KEY_ENV, raising=False)
    with pytest.raises(RuntimeError):
        M.run_task("x", "code-reviewer", transport=t)
    assert t.payloads == []


def test_tool_calls_in_a_response_are_an_error_not_an_action():
    with pytest.raises(RuntimeError):
        M.run_task("x", None, transport=FakeTransport(tool_calls=[{"function": {"name": "rm"}}]), key=SENTINEL_KEY)


def test_a_tampered_skill_file_makes_library_loading_fail(tmp_path):
    src = tmp_path / "sources"
    rel = json.loads((ROOT / "skills/library.json").read_text())["skills"][1]["file"]
    (src / pathlib.Path(rel).parent).mkdir(parents=True)
    (src / rel).write_bytes((ROOT / "skills/sources" / rel).read_bytes().replace(b"Code", b"code", 1))
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"skills": [json.loads((ROOT / "skills/library.json").read_text())["skills"][1]]}))
    with pytest.raises(RawSkillError):
        L.load_library(manifest, src)


def test_duplicate_ids_and_incomplete_entries_are_rejected(tmp_path):
    entry = json.loads((ROOT / "skills/library.json").read_text())["skills"][0]
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"skills": [entry, entry]}))
    with pytest.raises(RawSkillError):
        L.load_library(m)
    m.write_text(json.dumps({"skills": [{k: v for k, v in entry.items() if k != "blob_sha"}]}))
    with pytest.raises(RawSkillError):
        L.load_library(m)


def _imports(path):
    out = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.level == 0:
            out.add((n.module or "").split(".")[0])
    return out


def test_runtime_modules_have_no_governance_imports_and_no_authority_types():
    banned = {"governance", "runtime", "assurance", "evidence", "audit", "claims", "subprocess"}
    for f in (ROOT / "skills/library.py", ROOT / "skilleval/manual_runtime.py", ROOT / "skilleval/manual_server.py"):
        assert not (_imports(f) & banned), f
        names = {n.name for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))) if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
        for forbidden in ("AuthorizedAction", "Capability", "ExecutionEnvelope", "Tool"):
            assert forbidden not in names


@pytest.mark.parametrize("pkg", ["governance", "runtime", "assurance", "evidence", "audit", "claims"])
def test_no_protected_package_imports_the_manual_runtime(pkg):
    d = ROOT / pkg
    if not d.is_dir():
        pytest.skip("package not present")
    for f in d.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "skills.library" not in text and "manual_runtime" not in text and "manual_server" not in text, f
