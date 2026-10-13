"""Minimal restart-safe SQLite backing for generic tool governance.

This module persists only authorization identity, single-use consumption, and
receipt/evidence identity. It is deliberately not a workflow or replay store.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from .tool_invocation import ToolInvocation


class SQLiteToolStateStore:
    """Transactional backing for one IAOS tool-governance authority."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    invocation_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL,
                    tool_namespace TEXT NOT NULL,
                    arguments_digest TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    context TEXT NOT NULL,
                    effect_class TEXT NOT NULL,
                    consumed_execution_id TEXT,
                    receipt_status TEXT NOT NULL DEFAULT 'UNKNOWN_OR_INCOMPLETE',
                    receipt_json TEXT,
                    receipt_id TEXT,
                    result_digest TEXT,
                    evidence_mode TEXT NOT NULL DEFAULT 'DIGEST_ONLY',
                    structured_evidence_json TEXT,
                    receipt_timestamp REAL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    hash TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(authorizations)")}
            for name, definition in (
                ("invocation_id", "TEXT NOT NULL DEFAULT ''"),
                ("receipt_id", "TEXT"), ("result_digest", "TEXT"),
                ("evidence_mode", "TEXT NOT NULL DEFAULT 'DIGEST_ONLY'"),
                ("structured_evidence_json", "TEXT"), ("receipt_timestamp", "REAL"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE authorizations ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _record_hash(body: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _json_digest(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def _append_evidence(self, db: sqlite3.Connection, kind: str, intent_id: str,
                         payload: Mapping[str, Any]) -> None:
        previous = db.execute("SELECT hash FROM evidence ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = previous["hash"] if previous else "0" * 64
        payload_json = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        body = {"kind": kind, "intent_id": intent_id, "payload_json": payload_json, "prev_hash": prev_hash}
        digest = self._record_hash(body)
        db.execute(
            "INSERT INTO evidence(kind, intent_id, payload_json, prev_hash, hash) VALUES (?, ?, ?, ?, ?)",
            (kind, intent_id, payload_json, prev_hash, digest),
        )

    def _insert_authorization(self, db: sqlite3.Connection, invocation: ToolInvocation,
                              authorization_id: str, execution_id: str,
                              consumed_execution_id: str | None) -> None:
        db.execute(
            """INSERT INTO authorizations(
                authorization_id, invocation_id, execution_id, tool_name, tool_namespace,
                arguments_digest, resource, actor, context, effect_class,
                consumed_execution_id, evidence_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (authorization_id, invocation.invocation_id, execution_id, invocation.tool_name,
             invocation.tool_namespace, invocation.arguments_digest, invocation.resource,
             invocation.actor, invocation.context, invocation.effect_class,
             consumed_execution_id or "", invocation.evidence_mode),
        )

    def authorize_observation(self, invocation: ToolInvocation, authorization_id: str,
                              execution_id: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert_authorization(db, invocation, authorization_id, execution_id, None)
            self._append_evidence(db, "AUTHORIZATION", execution_id, {
                "action": "tool.invoke", "invocation_id": invocation.invocation_id,
                "tool_name": invocation.tool_name, "resource": invocation.resource,
                "arguments_digest": invocation.arguments_digest,
                "effect_class": invocation.effect_class, "evidence_mode": invocation.evidence_mode,
                "authorization_id": authorization_id,
            })
            db.commit()

    def authorize_and_consume(self, invocation: ToolInvocation, authorization_id: str,
                              execution_id: str) -> None:
        """Atomically persist authorization and its first consumption."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert_authorization(db, invocation, authorization_id, execution_id, execution_id)
            self._append_evidence(db, "AUTHORIZATION", execution_id, {
                "action": "tool.invoke", "tool_name": invocation.tool_name,
                "invocation_id": invocation.invocation_id,
                "tool_namespace": invocation.tool_namespace, "resource": invocation.resource,
                "arguments_digest": invocation.arguments_digest, "actor": invocation.actor,
                "context": invocation.context, "effect_class": invocation.effect_class,
                "authorization_id": authorization_id,
            })
            db.commit()

    def consume_existing(self, authorization_id: str, execution_id: str) -> bool:
        """Attempt a second consume; the durable CAS denies consumed records."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE authorizations SET consumed_execution_id = ? "
                "WHERE authorization_id = ? AND (consumed_execution_id IS NULL OR consumed_execution_id = '')",
                (execution_id, authorization_id),
            ).rowcount
            db.commit()
            return changed == 1

    def authorization(self, authorization_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM authorizations WHERE authorization_id = ?", (authorization_id,)).fetchone()
        return dict(row) if row else None

    def record_receipt(self, authorization: Mapping[str, Any], result_identity: Mapping[str, Any],
                       *, result_digest: str, evidence_mode: str,
                       structured_evidence: Any = None) -> dict[str, Any]:
        auth_id = authorization["authorization_id"]
        execution_id = authorization["execution_id"]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM authorizations WHERE authorization_id = ?", (auth_id,)
            ).fetchone()
            if row is None or row["execution_id"] != execution_id:
                db.rollback()
                raise ValueError("unknown_authorization")
            if row["receipt_status"] == "COMPLETE":
                db.rollback()
                raise ValueError("receipt_already_recorded")
            if evidence_mode not in ("DIGEST_ONLY", "STRUCTURED") or row["evidence_mode"] != evidence_mode:
                db.rollback()
                raise ValueError("receipt_evidence_mode_mismatch")
            receipt_id = f"tool-receipt-{uuid.uuid4().hex}"
            identity_json = json.dumps(dict(result_identity), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            structured_json = None
            if evidence_mode == "STRUCTURED":
                if structured_evidence is None or self._json_digest(structured_evidence) != result_digest:
                    db.rollback()
                    raise ValueError("structured_evidence_digest_mismatch")
                try:
                    structured_json = json.dumps(structured_evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                except (TypeError, ValueError) as exc:
                    db.rollback()
                    raise ValueError("invalid_structured_evidence") from exc
            db.execute(
                "UPDATE authorizations SET receipt_status = 'COMPLETE', receipt_json = ?, "
                "receipt_id = ?, result_digest = ?, structured_evidence_json = ?, receipt_timestamp = ? "
                "WHERE authorization_id = ?",
                (identity_json, receipt_id, result_digest, structured_json, time.time(), auth_id),
            )
            self._append_evidence(db, "EXECUTION_RESULT", execution_id, {
                "action": "tool.invoke", "tool_name": row["tool_name"],
                "invocation_id": row["invocation_id"],
                "resource": row["resource"], "arguments_digest": row["arguments_digest"],
                "authorization_id": auth_id, "execution_id": execution_id,
                "result_identity": dict(result_identity), "receipt_id": receipt_id,
                "result_digest": result_digest, "evidence_mode": evidence_mode,
            })
            db.commit()
            return {"receipt_id": receipt_id, "invocation_id": row["invocation_id"],
                    "result_digest": result_digest, "evidence_mode": evidence_mode}

    def receipt(self, authorization_id: str) -> dict[str, Any] | None:
        record = self.authorization(authorization_id)
        if record is None or record["receipt_status"] != "COMPLETE":
            return None
        result = {
            "receipt_id": record["receipt_id"],
            "invocation_id": record["invocation_id"],
            "authorization_id": authorization_id,
            "execution_id": record["execution_id"],
            "tool_name": record["tool_name"],
            "resource": record["resource"],
            "arguments_digest": record["arguments_digest"],
            "result_identity": json.loads(record["receipt_json"]),
        }
        if record["result_digest"] is not None:
            result["result_digest"] = record["result_digest"]
        if record["evidence_mode"] == "STRUCTURED":
            result["structured_evidence"] = json.loads(record["structured_evidence_json"])
        return result

    def receipt_by_invocation(self, invocation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT authorization_id FROM authorizations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
        return self.receipt(row["authorization_id"]) if row else None

    def evidence_events(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM evidence ORDER BY seq").fetchall()
        return [
            {"seq": row["seq"] - 1, "kind": row["kind"], "intent_id": row["intent_id"],
             "prev_hash": row["prev_hash"], "hash": row["hash"], **json.loads(row["payload_json"])}
            for row in rows
        ]

    def ui_authorizations(self) -> list[dict[str, Any]]:
        """Return a sanitized, read-only view for the product UI."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT authorization_id, invocation_id, execution_id, tool_name,
                          tool_namespace, arguments_digest, resource, effect_class,
                          consumed_execution_id, receipt_status, receipt_id,
                          evidence_mode, receipt_timestamp
                   FROM authorizations ORDER BY rowid DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def ui_receipts(self) -> list[dict[str, Any]]:
        """Return receipt identities without exposing credentials or raw payloads."""
        with self._connect() as db:
            ids = [row["authorization_id"] for row in db.execute(
                "SELECT authorization_id FROM authorizations WHERE receipt_status = 'COMPLETE' ORDER BY rowid DESC"
            ).fetchall()]
        return [receipt for auth_id in ids if (receipt := self.receipt(auth_id)) is not None]
