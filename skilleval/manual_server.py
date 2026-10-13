"""Loopback bridge for the Skill UI: exposes list/run of the manual Skill runtime over HTTP on 127.0.0.1 only.

  GET  /skills     -> {"skills": [{id, name, source}]}
  GET  /workspace  -> {"root": str|null}   (the only folder files can be read from; null = file context disabled)
  POST /run        -> {"task": str, "skill_id": str|null, "files": [str], "document": str|null}
                      ->  run_task result (+ skill_name, files, document)

"files" is 0..MAX_FILES paths RELATIVE to the workspace root. The bridge never reads them itself: EACH read goes through
the existing governance pipeline (skilleval.governed_read: fs.read authorization, bound handle) and only the authorized
text reaches the model. One denied/missing/oversized/unreadable file aborts the whole request before any model call
(no partial context); the summed content is capped at MODEL_CONTEXT_AGGREGATE_LIMIT chars.

"document" is ONE relative path to a .pdf/.png/.jpg/.jpeg. Its bytes come ONLY from the governed fs.read_bytes; the
interpretation (PDF text via pypdf, image attached as-is) happens here, outside Governance, on those authorized bytes.
An unsupported extension is refused BEFORE any read; any other failure aborts the request before the model call.

The browser never sees the credential: AGNES_API_KEY is read from this process's environment at call time.
Guards: loopback Host only, loopback Origin only (when sent), JSON content-type on POST, body/task size caps,
unknown skill id -> 404 before any model call. No tools are exposed to the model; errors never echo upstream bodies.
Start:  python -m skilleval.manual_server [--port 8765]
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from skills.library import SkillLibrary, UnknownSkillError, load_library
from skilleval import document_context as D
from skilleval import manual_runtime as M
from skilleval.governed_read import FileTooLarge, GovernedFileReader, ReadDenied, ReadFailed
from skilleval import raw_smoke as RS

HOST = "127.0.0.1"
PORT = 8765
MAX_BODY = 65536
MAX_TASK = 20000
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DOCUMENT_STATUS = {"unsupported_document": 415, "invalid_document": 422, "document_no_text": 422, "context_too_large": 413,
                   "document_support_unavailable": 503}


def _host_name(host_header: str) -> str:
    h = (host_header or "").strip()
    if h.startswith("["):
        return h[1:].split("]")[0]
    return h.split(":")[0]


class Bridge(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, library: SkillLibrary, transport=RS.http_transport, key: str = None, model: str = M.DEFAULT_MODEL,
                 reader: GovernedFileReader = None):
        super().__init__(addr, Handler)
        self.library, self.transport, self.key, self.model, self.reader = library, transport, key, model, reader


class Handler(BaseHTTPRequestHandler):
    server_version = "IAOSSkillBridge"

    def log_message(self, *a):  # keep task text and headers out of logs
        pass

    def _drain(self):
        """Consume a modest unread request body before answering an early error, so the client sees the status, not a reset."""
        if self.command == "POST" and not getattr(self, "_body_read", False):
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if 0 < n <= 4 * MAX_BODY:
                self.rfile.read(n)
            self.close_connection = True
        self._body_read = True

    def _send(self, code: int, obj: dict):
        self._drain()
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        if _host_name(self.headers.get("Host", "")) not in LOOPBACK:
            self._send(403, {"error": "forbidden_host"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and (urlparse(origin).hostname or "") not in LOOPBACK:
            self._send(403, {"error": "forbidden_origin"})
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        if self.path.split("?")[0] == "/skills":
            self._send(200, {"skills": self.server.library.list_skills()})
        elif self.path.split("?")[0] == "/workspace":
            r = self.server.reader
            self._send(200, {"root": r.root if r else None})
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self):
        if not self._guard():
            return
        if self.path.split("?")[0] != "/run":
            return self._send(404, {"error": "not_found"})
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return self._send(415, {"error": "json_required"})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._send(400, {"error": "invalid"})
        if length < 0 or length > MAX_BODY:
            return self._send(413, {"error": "too_large"})
        raw_body = self.rfile.read(length)
        self._body_read = True
        try:
            req = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"error": "invalid"})
        task = req.get("task") if isinstance(req, dict) else None
        skill_id = req.get("skill_id") if isinstance(req, dict) else None
        refs = req.get("files", []) if isinstance(req, dict) else None
        doc_ref = req.get("document") if isinstance(req, dict) else None
        if (not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK or not (skill_id is None or isinstance(skill_id, str))
                or not isinstance(refs, list) or len(refs) > M.MAX_FILES
                or not all(isinstance(r, str) and r.strip() for r in refs) or len(set(refs)) != len(refs)
                or not (doc_ref is None or (isinstance(doc_ref, str) and doc_ref.strip()))):
            return self._send(400, {"error": "invalid"})
        lib = self.server.library
        skill_name = None
        if skill_id is not None:
            try:
                skill_name = lib.get(skill_id).name
            except UnknownSkillError:
                return self._send(404, {"error": "unknown_skill"})
        key = self.server.key or os.environ.get(RS.KEY_ENV)
        if not key:
            return self._send(503, {"error": "not_configured"})
        context, files_meta, total = [], [], 0
        image, doc_meta, doc_kind = None, None, None
        if doc_ref is not None:
            try:
                doc_kind = D.kind_for(doc_ref)  # extension decided before ANY read is requested
            except D.DocumentError as e:
                return self._send(DOCUMENT_STATUS[e.code], {"error": e.code, "path": doc_ref})
        if refs or doc_ref is not None:
            reader = self.server.reader
            if reader is None:
                return self._send(503, {"error": "workspace_not_configured"})
        if refs:
            for i, ref in enumerate(refs):
                try:
                    gf = reader.read(ref)  # governed fs.read, one per file; nothing is read if governance denies
                except ReadDenied as e:
                    missing = "resource_not_found" in e.reason
                    return self._send(404 if missing else 403, {"error": "file_not_found" if missing else "read_denied",
                                                                "detail": e.reason, "index": i, "path": ref})
                except FileTooLarge:
                    return self._send(413, {"error": "file_too_large", "index": i, "path": ref})
                except ReadFailed:
                    return self._send(422, {"error": "file_unreadable", "index": i, "path": ref})
                total += gf.chars
                if total > M.MODEL_CONTEXT_AGGREGATE_LIMIT:
                    return self._send(413, {"error": "context_too_large", "index": i, "path": ref})
                context.append(M.ContextFile(gf.relative, gf.content))
                files_meta.append({"path": gf.relative, "sha256": gf.sha256, "chars": gf.chars, "governed_action": "fs.read",
                                   "authorization_event_id": gf.authorization_event_id, "execution_id": gf.execution_id,
                                   "bound_handle_reads": gf.bound_handle_reads,
                                   "path_open_calls_during_exec": gf.path_open_calls_during_exec})
        if doc_ref is not None:
            try:
                gb = reader.read_bytes(doc_ref)  # governed fs.read_bytes; nothing is read if governance denies
            except ReadDenied as e:
                missing = "resource_not_found" in e.reason
                return self._send(404 if missing else 403, {"error": "file_not_found" if missing else "read_denied",
                                                            "detail": e.reason, "path": doc_ref})
            except FileTooLarge:
                return self._send(413, {"error": "document_too_large", "path": doc_ref})
            except ReadFailed:
                return self._send(422, {"error": "file_unreadable", "path": doc_ref})
            pages = chars = None
            try:
                D.check_signature(doc_kind, gb.data)
                if doc_kind == "pdf":
                    pdf = D.extract_pdf(gb.data, M.MODEL_CONTEXT_AGGREGATE_LIMIT - total)
                    context.append(M.ContextFile(gb.relative + " (text extracted from PDF)", pdf.text))
                    pages, chars = pdf.pages, len(pdf.text)
                else:
                    image = M.ContextImage(gb.relative, doc_kind, gb.data)
            except D.DocumentError as e:
                return self._send(DOCUMENT_STATUS[e.code], {"error": e.code, "path": doc_ref})
            doc_meta = {"path": gb.relative, "kind": doc_kind, "bytes": gb.size, "sha256": gb.sha256, "pages": pages,
                        "extracted_chars": chars, "governed_action": "fs.read_bytes",
                        "authorization_event_id": gb.authorization_event_id, "execution_id": gb.execution_id,
                        "bound_handle_reads": gb.bound_handle_reads, "path_open_calls_during_exec": gb.path_open_calls_during_exec}
        try:
            out = M.run_task(task, skill_id, library=lib, model=self.server.model, transport=self.server.transport, key=key,
                             context=context, image=image)
        except HTTPError as e:
            return self._send(502, {"error": "upstream", "detail": f"HTTP {e.code}"})
        except (URLError, TimeoutError, OSError):
            return self._send(502, {"error": "upstream", "detail": "unreachable"})
        except RuntimeError:
            return self._send(502, {"error": "upstream", "detail": "unexpected response"})
        out["skill_name"] = skill_name
        out["files"] = files_meta
        out["document"] = doc_meta
        self._send(200, out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="manual_server")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--workspace", default=None, help="folder files may be read from (relative paths only); omit to disable file context")
    a = p.parse_args(argv)
    reader = None
    if a.workspace:
        if not os.path.isdir(a.workspace):  # operator configuration check at startup, not part of any read
            raise SystemExit("--workspace is not a directory")
        reader = GovernedFileReader(a.workspace)
    srv = Bridge((HOST, a.port), load_library(), reader=reader)
    print(f"IAOS skill bridge on http://{HOST}:{a.port}  (credential in environment: {'yes' if os.environ.get(RS.KEY_ENV) else 'NO'};"
          f" workspace: {reader.root if reader else 'none'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
