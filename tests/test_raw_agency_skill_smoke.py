"""IAOS_RAW_AGENCY_SKILL_SMOKE_GATE (offline part): pinned raw content, unmodified, reaches cognition only.

The live model calls are recorded separately (skilleval/raw_smoke_run/). These tests use a fake transport.
KNOWN LIMIT: "no authority" is shown structurally (no tool in the request, no object type, no governance imports);
it cannot prove anything about a model's own behavior beyond the recorded text-only outputs.
"""
import ast
import json
import pathlib

import pytest

from skilleval import raw_smoke as S
from skills import raw as R

ROOT = pathlib.Path(__file__).resolve().parents[1]
REV = "83294689da3832c0a9f223221148c411fd3eacc0"
BLOB = "a81d24957215b077a655111936ed4ff6a461d92d"
PATH = "engineering/engineering-incident-response-commander.md"
FILE = ROOT / "skills/sources/agency-agents" / REV / "engineering-incident-response-commander.md"
SENTINEL_KEY = "sk-test-SENTINEL-0123456789"


def _raw():
    return R.load_raw_skill(FILE, "msitarzewski/agency-agents", REV, PATH, BLOB, "MIT")


def test_raw_content_matches_the_pinned_blob_and_a_changed_byte_is_refused(tmp_path):
    raw = _raw()
    assert R.git_blob_sha1(raw.text.encode("utf-8")) == BLOB
    bad = tmp_path / "x.md"
    bad.write_bytes(FILE.read_bytes().replace(b"Incident", b"incident", 1))
    with pytest.raises(R.RawSkillError):
        R.load_raw_skill(bad, "r", REV, PATH, BLOB, "MIT")


def test_crlf_checkout_is_accepted_because_content_is_the_lf_blob(tmp_path):
    crlf = tmp_path / "c.md"
    crlf.write_bytes(FILE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert R.load_raw_skill(crlf, "r", REV, PATH, BLOB, "MIT").text == _raw().text


def test_skill_context_is_the_original_markdown_unchanged():
    raw = _raw()
    assert R.raw_skill_context(raw) == raw.text
    prompt = S.build_prompt(S.TASK, raw)
    assert raw.text in prompt and prompt.count(raw.text) == 1


def test_control_prompt_is_the_bare_task_and_differs_from_the_skill_prompt_only_by_the_skill_block():
    raw = _raw()
    assert S.build_prompt(S.TASK) == S.TASK
    assert S.build_prompt(S.TASK, raw) == S.SKILL_LEAD + raw.text + "\n\n=== TASK ===\n" + S.TASK


def test_raw_source_is_immutable_and_a_non_source_is_rejected():
    raw = _raw()
    with pytest.raises(Exception):
        raw.text = "x"
    with pytest.raises(TypeError):
        R.raw_skill_context("markdown")


def _fake(captured):
    def transport(payload, key):
        captured.append((payload, key))
        return 200, {"X-Request-Id": "req-123"}, {"id": "resp-1", "model": "agnes-test-served",
                                                   "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                                   "usage": {"total_tokens": 1}}
    return transport


def test_raw_skill_text_reaches_the_model_request_and_the_request_has_no_tool_surface(tmp_path):
    cap = []
    raw = _raw()
    res = S.run_smoke(raw, "agnes-test", tmp_path, _fake(cap), SENTINEL_KEY)
    assert len(cap) == 2
    control, treated = cap[0][0], cap[1][0]
    assert raw.text not in json.dumps(control) and raw.text in treated["messages"][0]["content"]
    for payload, _ in cap:
        assert set(payload) <= S.ALLOWED_REQUEST_KEYS
        for forbidden in ("tools", "functions", "tool_choice", "function_call"):
            assert forbidden not in payload
    assert all(c["tool_calls_in_response"] is None for c in res["calls"])


def test_key_is_never_written_to_the_record_and_metadata_is_captured(tmp_path):
    S.run_smoke(_raw(), "agnes-test", tmp_path, _fake([]), SENTINEL_KEY)
    text = (tmp_path / "smoke_record.json").read_text(encoding="utf-8")
    assert SENTINEL_KEY not in text
    rec = json.loads(text)["calls"][1]
    assert (rec["provider"], rec["requested_model"], rec["returned_model"], rec["request_id_header"]) == (
        "Agnes AI", "agnes-test", "agnes-test-served", "req-123")
    assert len(rec["prompt_sha256"]) == 64 and rec["params_sent"]["temperature"] == 0.0 and rec["seed"] == "not sent"


def test_missing_credential_fails_closed_without_calling_the_transport(monkeypatch):
    monkeypatch.delenv(S.KEY_ENV, raising=False)
    cap = []
    with pytest.raises(RuntimeError):
        S.call("x", "p", "m", _fake(cap))
    assert cap == []


def _imports(path):
    out = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.level == 0:
            out.add((n.module or "").split(".")[0])
    return out


def test_raw_modules_import_no_governance_runtime_assurance_evidence_audit_or_claims_and_define_no_authority_types():
    banned = {"governance", "runtime", "assurance", "evidence", "audit", "claims", "subprocess", "os.system"}
    for f in (ROOT / "skills/raw.py", ROOT / "skilleval/raw_smoke.py"):
        assert not (_imports(f) & banned), f
        names = {n.name for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))) if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
        for forbidden in ("AuthorizedAction", "Capability", "ExecutionEnvelope", "Tool"):
            assert forbidden not in names


@pytest.mark.parametrize("pkg", ["governance", "runtime", "assurance", "evidence", "audit", "claims"])
def test_no_protected_package_imports_the_raw_skill_modules(pkg):
    d = ROOT / pkg
    if not d.is_dir():
        pytest.skip("package not present")
    for f in d.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "skills.raw" not in text and "raw_smoke" not in text, f
