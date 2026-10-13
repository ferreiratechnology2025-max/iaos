"""Minimal local HTTP gateway for generic IAOS tool governance.

With ``IAOS_TOOL_STATE_DB`` configured, authorization, consumption and receipts
use the restart-safe SQLite backing. Without it, the legacy in-memory mode is
retained for isolated B1 tests only.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from governance.durable_tool_state import SQLiteToolStateStore
from governance.tool_kernel import ToolInvocationKernel

_STATE_DB = os.environ.get("IAOS_TOOL_STATE_DB")
KERNEL = ToolInvocationKernel(
    durable_store=SQLiteToolStateStore(_STATE_DB) if _STATE_DB else None
)


class ToolGatewayHandler(BaseHTTPRequestHandler):
    def _json(self, value: object, status: int = 200) -> None:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        origin = self.headers.get("Origin")
        if origin in {"http://127.0.0.1:5173", "http://localhost:5173"}:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._json({}, 204)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/ui/status":
            self._json({
                "gateway": "connected",
                "effect_mode": "B2_EFFECTS_ENABLED" if os.environ.get("IAOS_B2_ENABLE_EFFECTS") else "OBSERVE_ONLY",
                "durable_state": bool(_STATE_DB),
                "state_db_configured": bool(_STATE_DB),
            })
            return
        if self.path == "/ui/invocations":
            self._json({"invocations": KERNEL.durable_store.ui_authorizations() if KERNEL.durable_store else []})
            return
        if self.path == "/ui/authorizations":
            self._json({"authorizations": KERNEL.durable_store.ui_authorizations() if KERNEL.durable_store else []})
            return
        if self.path == "/ui/consumptions":
            records = KERNEL.durable_store.ui_authorizations() if KERNEL.durable_store else []
            self._json({"consumptions": [
                {"authorization_id": r["authorization_id"], "execution_id": r["execution_id"],
                 "consumed": bool(r["consumed_execution_id"])} for r in records
            ]})
            return
        if self.path == "/ui/receipts":
            self._json({"receipts": KERNEL.durable_store.ui_receipts() if KERNEL.durable_store else []})
            return
        if self.path == "/ui/evidence":
            self._json({"evidence": KERNEL.evidence_events()})
            return
        if self.path.startswith("/ui/invocations/"):
            invocation_id = self.path.rsplit("/", 1)[-1]
            records = KERNEL.durable_store.ui_authorizations() if KERNEL.durable_store else []
            record = next((r for r in records if r["invocation_id"] == invocation_id), None)
            if record is None:
                self._json({"error": "invocation_not_found"}, 404)
            else:
                self._json({"invocation": record})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/tool-invocation", "/tool-receipt"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            if self.path == "/tool-receipt":
                result = KERNEL.record_receipt(payload)
                print(
                    f"IAOS_RECEIPT authorization_id_present={'yes' if payload.get('authorization_id') else 'no'} "
                    f"execution_id_present={'yes' if payload.get('execution_id') else 'no'}",
                    flush=True,
                )
            elif self.path == "/tool-invocation":
                result = KERNEL.authorize_and_consume(
                    payload, allow_effects=bool(os.environ.get("IAOS_B2_ENABLE_EFFECTS"))
                )
            else:
                self.send_error(404)
                return
            print(
                f"IAOS_MEDIATED tool={payload.get('tool_name', '<unknown>')} "
                f"effect_class={payload.get('effect_class', '<unknown>')} "
                f"digest={payload.get('arguments_digest', '<missing>')} "
                f"actor_present={'yes' if payload.get('actor') else 'no'} "
                f"context_present={'yes' if payload.get('context') else 'no'}",
                flush=True,
            )
            status = 200
        except (ValueError, json.JSONDecodeError, TypeError) as exc:
            result = {"status": "REJECTED", "reason": str(exc)}
            status = 400
        self._json(result, status)

    def log_message(self, *_args: object) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8791) -> None:
    ThreadingHTTPServer((host, port), ToolGatewayHandler).serve_forever()


if __name__ == "__main__":
    serve()
