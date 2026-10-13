"""IAOS_GOVERNED_DOCUMENT_CONTEXT_V1: ONE PDF or image (+ optional Skill + task). The bytes come ONLY from the governed
``fs.read_bytes`` (open-first bound handle); PDF text extraction / image attachment happen outside Governance on those
authorized bytes. Fake model transport; real filesystem under tmp_path; no network.
KNOWN LIMITS: the fs.read_bytes executor has no size limit (the bridge refuses to forward/parse > MAX_DOCUMENT_BYTES);
no OCR (a PDF without a text layer is rejected, never silently OCR'd); lint-style checks catch accidental violations only.
"""
import base64
import hashlib
import http.client
import io
import json
import os
import pathlib
import struct
import threading
import zlib

import pytest

pypdf = pytest.importorskip("pypdf")

from skilleval import document_context as D
from skilleval import governed_read as G
from skilleval import manual_runtime as M
from skilleval import manual_server as B
from skills.library import load_library

KEY = "sk-test-SENTINEL-0123456789"
PDF_P1 = "Clausula 1 multa de 20 por cento sobre o valor do contrato"
PDF_P2 = "Clausula 2 foro da comarca de Sao Paulo"


def make_pdf(pages):
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % (4 + 2 * i) for i in range(len(pages))), len(pages)),
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        stream = "BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i))
        objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode("latin-1")
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode()
    for o in offs:
        out += ("%010d 00000 n \n" % o).encode()
    return out + ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)).encode()


def make_png():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([200, 30, 30]) * 8 for _ in range(8))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PDF = make_pdf([PDF_P1, PDF_P2])
PNG = make_png()
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + bytes(range(256)) + b"\xff\xd9"
NOTES = "notas de texto\n"


def blank_pdf():
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def encrypted_pdf():
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("pw")
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


@pytest.fixture()
def ws(tmp_path):
    base = pathlib.Path(os.path.realpath(tmp_path))
    root = base / "ws"
    root.mkdir()
    for name, data in {"contrato.pdf": PDF, "scan.pdf": blank_pdf(), "bad.pdf": b"%PDF-1.4\nthis is not a pdf at all\x00\x01",
                       "locked.pdf": encrypted_pdf(), "pic.png": PNG, "pic.jpg": JPG, "pic.jpeg": JPG, "notes.txt": NOTES.encode(),
                       "doc.docx": b"PK\x03\x04docx", "fake.pdf": PNG, "image.png": JPG}.items():
        (root / name).write_bytes(data)
    (root / "sub").mkdir()
    (root / "sub" / "inner.pdf").write_bytes(PDF)
    (base / "outside.pdf").write_bytes(PDF)
    (base / "ws_evil").mkdir()
    (base / "ws_evil" / "secret.pdf").write_bytes(PDF)
    return base, root


def snapshot(base):
    return sorted((str(p.relative_to(base)), p.stat().st_size, p.stat().st_mtime_ns) for p in base.rglob("*"))


# ---- document layer (pure, bytes in / data out) ---------------------------------------------------------------------

@pytest.mark.parametrize("name,kind", [("a.pdf", "pdf"), ("A.PDF", "pdf"), ("d/x.png", "image/png"), ("x.JPG", "image/jpeg"),
                                       ("sub\\y.jpeg", "image/jpeg")])
def test_supported_extensions(name, kind):
    assert D.kind_for(name) == kind


@pytest.mark.parametrize("name", ["a.docx", "a.txt", "noext", "evil.pdf.exe", "a.gif", "a.webp", "a.", ".pdf.bak", "dir.pdf/x"])
def test_unsupported_extensions_are_refused(name):
    with pytest.raises(D.DocumentError) as e:
        D.kind_for(name)
    assert e.value.code == "unsupported_document"


def test_signature_must_match_the_claimed_type():
    D.check_signature("pdf", PDF)
    D.check_signature("image/png", PNG)
    D.check_signature("image/jpeg", JPG)
    for kind, data in (("pdf", PNG), ("image/png", JPG), ("image/jpeg", PNG), ("pdf", b"")):
        with pytest.raises(D.DocumentError) as e:
            D.check_signature(kind, data)
        assert e.value.code == "invalid_document"


def test_pdf_text_is_extracted_from_the_in_memory_bytes():
    out = D.extract_pdf(PDF, 100_000)
    assert out.pages == 2 and PDF_P1 in out.text and PDF_P2 in out.text
    assert out.text.index("--- page 1 ---") < out.text.index(PDF_P1) < out.text.index("--- page 2 ---") < out.text.index(PDF_P2)


@pytest.mark.parametrize("data,code", [(blank_pdf(), "document_no_text"), (b"%PDF-1.4\nnot a pdf\x00", "invalid_document"),
                                       (encrypted_pdf(), "invalid_document")])
def test_unusable_pdfs_are_rejected_without_ocr_or_fallback(data, code):
    with pytest.raises(D.DocumentError) as e:
        D.extract_pdf(data, 100_000)
    assert e.value.code == code


def test_pdf_text_over_the_limit_is_rejected():
    with pytest.raises(D.DocumentError) as e:
        D.extract_pdf(PDF, 50)
    assert e.value.code == "context_too_large"
    with pytest.raises(D.DocumentError):
        D.extract_pdf(PDF, 0)


def test_document_layer_has_no_filesystem_network_or_governance_imports():
    import ast
    tree = ast.parse((pathlib.Path(D.__file__)).read_text(encoding="utf-8"))
    mods = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"io", "dataclasses", "pypdf"}, mods
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") in {"open", "exec", "eval"}]


# ---- governed raw read -----------------------------------------------------------------------------------------------

def test_read_bytes_returns_the_raw_bytes_of_the_bound_object_through_governance(ws):
    _, root = ws
    rd = G.GovernedFileReader(str(root))
    gb = rd.read_bytes("contrato.pdf")
    assert gb.data == PDF and gb.size == len(PDF) and gb.sha256 == hashlib.sha256(PDF).hexdigest()
    assert gb.bound_handle_reads == 1 and gb.path_open_calls_during_exec == 0
    log = rd.effect_log()
    assert [e["opcode"] for e in log] == ["FS_READ_BYTES"] and log[0]["result_digest"] == gb.sha256
    kinds = [e.get("kind") for e in rd.store.events()]
    assert kinds == ["AUTHORIZATION", "EXECUTION_RESULT"] and not any(PDF[:20].hex() in repr(e) for e in rd.store.events())
    assert rd.read_bytes("sub/inner.pdf").data == PDF


@pytest.mark.parametrize("raw", ["../outside.pdf", "../ws_evil/secret.pdf", "sub/../../outside.pdf"])
def test_unauthorized_documents_are_rejected_and_nothing_is_read(ws, raw):
    _, root = ws
    rd = G.GovernedFileReader(str(root))
    with pytest.raises(G.ReadDenied) as e:
        rd.read_bytes(raw)
    assert "resource_outside_root" in e.value.reason and rd.effect_log() == []


def test_missing_directory_invalid_and_oversized_documents(ws):
    base, root = ws
    rd = G.GovernedFileReader(str(root), max_bytes=100)
    for raw in ("nope.pdf", "sub", "", "  "):
        with pytest.raises(G.ReadDenied):
            rd.read_bytes(raw)
    with pytest.raises(G.FileTooLarge):
        rd.read_bytes("contrato.pdf")
    with pytest.raises(G.ReadDenied):
        rd.read_bytes(str(base / "outside.pdf"))


def test_text_read_is_unchanged_next_to_read_bytes(ws):
    _, root = ws
    rd = G.GovernedFileReader(str(root))
    assert rd.read("notes.txt").content == NOTES
    assert rd.read_bytes("notes.txt").data == NOTES.encode()
    with pytest.raises(G.ReadFailed):
        rd.read("pic.png")  # fs.read still refuses non-UTF-8; it was not widened


# ---- bridge ----------------------------------------------------------------------------------------------------------

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

    def make(reader="default", key=KEY, **kw):
        rd = G.GovernedFileReader(str(root), **kw) if reader == "default" else reader
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


def run(srv, **body):
    return call(srv, "POST", "/run", {"task": "analise", **body})


def test_pdf_plus_skill_reach_cognition_through_fs_read_bytes(bridge):
    srv, t, rd = bridge()
    st, out = run(srv, skill_id="code-reviewer", document="contrato.pdf")
    assert st == 200 and out["response"] == "resposta"
    content = t.payloads[0]["messages"][0]["content"]
    raw = load_library().get("code-reviewer").raw
    assert isinstance(content, str) and raw.text in content and PDF_P1 in content and PDF_P2 in content
    assert "contrato.pdf (text extracted from PDF)" in content and content.endswith("analise")
    assert not ({"tools", "functions", "tool_choice"} & set(t.payloads[0])) and KEY not in json.dumps(out)
    d = out["document"]
    assert d["path"] == "contrato.pdf" and d["kind"] == "pdf" and d["pages"] == 2 and d["governed_action"] == "fs.read_bytes"
    assert d["bytes"] == len(PDF) and d["sha256"] == hashlib.sha256(PDF).hexdigest() and d["extracted_chars"] > 0
    assert d["bound_handle_reads"] == 1 and d["path_open_calls_during_exec"] == 0
    assert [e["opcode"] for e in rd.effect_log()] == ["FS_READ_BYTES"] and out["context_image"] is None


@pytest.mark.parametrize("name,mime,data", [("pic.png", "image/png", PNG), ("pic.jpg", "image/jpeg", JPG), ("pic.jpeg", "image/jpeg", JPG)])
def test_image_bytes_reach_the_model_request_exactly(bridge, name, mime, data):
    srv, t, rd = bridge()
    st, out = run(srv, skill_id=None, document=name)
    assert st == 200
    payload = t.payloads[0]
    parts = payload["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url"] and "analise" in parts[0]["text"]
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:%s;base64," % mime) and base64.b64decode(url.split(",", 1)[1]) == data
    assert not ({"tools", "functions", "tool_choice"} & set(payload)) and set(payload) <= {"model", "messages", "temperature", "top_p", "max_tokens"}
    d = out["document"]
    assert d["kind"] == mime and d["bytes"] == len(data) and d["pages"] is None and d["governed_action"] == "fs.read_bytes"
    assert out["context_image"] == {"name": name, "mime": mime, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    assert [e["opcode"] for e in rd.effect_log()] == ["FS_READ_BYTES"] and KEY not in json.dumps(out)


def test_optional_skill_reaches_cognition_with_an_image(bridge):
    srv, t, _ = bridge()
    assert run(srv, skill_id="code-reviewer", document="pic.png")[0] == 200
    text = t.payloads[0]["messages"][0]["content"][0]["text"]
    assert load_library().get("code-reviewer").raw.text in text and M.IMAGE_LEAD in text and text.endswith("analise")


def test_document_and_text_files_can_travel_together(bridge):
    srv, t, rd = bridge()
    st, out = run(srv, files=["notes.txt"], document="contrato.pdf")
    assert st == 200
    c = t.payloads[0]["messages"][0]["content"]
    assert c.index(NOTES.strip()) < c.index(PDF_P1) < c.index("=== TASK ===")
    assert [e["opcode"] for e in rd.effect_log()] == ["FS_READ", "FS_READ_BYTES"] and [f["path"] for f in out["files"]] == ["notes.txt"]


def test_no_document_means_no_document_metadata_and_no_image(bridge):
    srv, t, _ = bridge()
    st, out = run(srv, skill_id="code-reviewer")
    assert st == 200 and out["document"] is None and out["context_image"] is None and isinstance(t.payloads[0]["messages"][0]["content"], str)


@pytest.mark.parametrize("doc", ["../outside.pdf", "../ws_evil/secret.pdf", "sub/../../outside.pdf"])
def test_unauthorized_document_is_403_and_never_reaches_the_model(bridge, ws, doc):
    base, _ = ws
    srv, t, rd = bridge()
    st, out = run(srv, document=doc)
    assert st == 403 and out["error"] == "read_denied" and "resource_outside_root" in out["detail"] and out["path"] == doc
    assert t.payloads == [] and rd.effect_log() == [] and "Clausula" not in json.dumps(out)
    assert run(srv, document=str(base / "outside.pdf"))[0] == 403 and t.payloads == []


def test_missing_document_is_404(bridge):
    srv, t, _ = bridge()
    st, out = run(srv, document="nope.pdf")
    assert st == 404 and out["error"] == "file_not_found" and t.payloads == []


def test_unsupported_extension_is_refused_before_any_read(bridge):
    srv, t, rd = bridge()
    for name in ("doc.docx", "notes.txt"):
        st, out = run(srv, document=name)
        assert st == 415 and out["error"] == "unsupported_document" and out["path"] == name
    assert t.payloads == [] and rd.effect_log() == [] and rd.bindings.open_calls() == 0  # not even a governed read was requested


@pytest.mark.parametrize("name,code", [("fake.pdf", "invalid_document"), ("image.png", "invalid_document"), ("bad.pdf", "invalid_document"),
                                       ("locked.pdf", "invalid_document"), ("scan.pdf", "document_no_text")])
def test_invalid_or_textless_documents_fail_before_the_model_with_no_ocr_fallback(bridge, name, code):
    srv, t, _ = bridge()
    st, out = run(srv, document=name)
    assert st == 422 and out["error"] == code and out["path"] == name and t.payloads == []


def test_pdf_text_over_the_context_limit_and_oversized_bytes_fail_before_the_model(bridge, monkeypatch):
    srv, t, _ = bridge()
    monkeypatch.setattr(M, "MODEL_CONTEXT_AGGREGATE_LIMIT", 60)
    st, out = run(srv, document="contrato.pdf")
    assert st == 413 and out["error"] == "context_too_large" and t.payloads == []
    monkeypatch.undo()
    srv2, t2, _ = bridge(max_bytes=100)
    st, out = run(srv2, document="contrato.pdf")
    assert st == 413 and out["error"] == "document_too_large" and t2.payloads == []


def test_one_rejected_item_aborts_the_whole_request(bridge):
    srv, t, rd = bridge()
    st, out = run(srv, files=["notes.txt"], document="../outside.pdf")
    assert st == 403 and t.payloads == [] and len(rd.effect_log()) == 1  # the text file was read, the model was never called
    st, out = run(srv, files=["../outside.pdf"], document="contrato.pdf")
    assert st == 403 and out["index"] == 0 and t.payloads == []


def test_unknown_skill_or_missing_credential_or_workspace_means_no_open_at_all(bridge):
    srv, t, rd = bridge()
    assert run(srv, skill_id="nope", document="contrato.pdf")[0] == 404
    srv2, t2, rd2 = bridge(key=None)
    assert run(srv2, document="contrato.pdf")[0] == 503
    srv3, t3, _ = bridge(reader=None)
    st, out = run(srv3, document="contrato.pdf")
    assert st == 503 and out["error"] == "workspace_not_configured"
    assert rd.bindings.open_calls() == 0 and rd2.bindings.open_calls() == 0 and t.payloads == t2.payloads == t3.payloads == []


@pytest.mark.parametrize("bad", ["", "  ", 5, ["a.pdf"], {"p": 1}])
def test_invalid_document_field_is_400(bridge, bad):
    srv, t, _ = bridge()
    assert run(srv, document=bad)[0] == 400 and t.payloads == []


def test_no_write_effect_whatever_happens(bridge, ws):
    base, _ = ws
    before = snapshot(base)
    srv, _, rd = bridge()
    for doc in ("contrato.pdf", "pic.png", "scan.pdf", "../outside.pdf", "doc.docx", "nope.pdf"):
        run(srv, document=doc)
    assert snapshot(base) == before and {e["opcode"] for e in rd.effect_log()} <= {"FS_READ_BYTES"}


def test_governed_read_module_still_has_no_filesystem_command_or_network_surface():
    import ast
    src = (pathlib.Path(G.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"os", "pathlib", "subprocess", "ctypes", "socket", "shutil", "tempfile", "io"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") in {"open", "exec", "eval"}]
    assert {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("fs.")} == {"fs.read", "fs.read_bytes"}
