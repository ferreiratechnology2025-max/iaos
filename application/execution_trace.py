"""Bounded, append-only execution diagnostics for one AgentLoop run.

This trace is diagnostic evidence only. It is deliberately separate from effect
evidence and verifier evidence, and it never contains prompts, raw page text,
secrets, or source values.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol


class ExecutionTraceSink(Protocol):
    def emit(self, event: Mapping[str, Any]) -> None:
        ...


def digest(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()


def sanitized_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only decision identity and bounded metadata; never persist raw input text."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key in {"text", "value", "secret", "token", "api_key", "password"}:
            text = value if isinstance(value, str) else str(value)
            out[key] = {"sha256": digest(text), "length": len(text)}
        elif key in {"ref", "source", "field", "key", "option"}:
            out[key] = value
        elif key == "url" and isinstance(value, str):
            out[key] = value[:512]
        else:
            out[key] = str(value)[:256]
    return out


_SENSITIVE_TEXT = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|computer[_-]?token|"
    r"brave[_-]?search[_-]?api[_-]?key|agnes[_-]?api[_-]?key|password|secret)"
    r"(\s*[:=]\s*)([\"']?)([^\s,\"'}]+)\3"
)
_AUTHORIZATION_BEARER = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,]+)")


def redact_sensitive_text(value: object) -> str:
    """Bounded text for provenance diagnostics; preserve action evidence, redact credentials."""
    text = value if isinstance(value, str) else str(value)
    text = _AUTHORIZATION_BEARER.sub(r"\1<REDACTED>", text[:12000])
    return _SENSITIVE_TEXT.sub(r"\1\2\3<REDACTED>\3", text)


class JsonlExecutionTrace:
    """A fail-closed-before-run JSONL sink with incremental flush after every event."""

    def __init__(self, path: str | os.PathLike[str], run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._order = 0
        self._stream = self.path.open("a", encoding="utf-8", newline="\n")

    def emit(self, event: Mapping[str, Any]) -> None:
        self._order += 1
        record = {
            "run_id": self.run_id,
            "order": self._order,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **dict(event),
        }
        self._stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()


class MemoryExecutionTrace:
    """Deterministic synthetic sink used by tests; mirrors the JSONL event shape."""

    def __init__(self, run_id: str = "synthetic") -> None:
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> None:
        self.events.append({"run_id": self.run_id, "order": len(self.events) + 1, **dict(event)})

    def close(self) -> None:
        return None
