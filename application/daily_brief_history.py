"""Persistent product history for Daily Brief runs.

This is deliberately separate from Governance evidence, audit reports, and
memory. It stores only the product result needed to revisit a completed run.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class DailyBriefRun:
    run_id: str
    requested_at: str
    completed_at: str
    status: str
    summary: str | None = None
    calendar_section: str | None = None
    email_section: str | None = None
    web_section: str | None = None
    scout_section: str | None = None
    failure_stage: str | None = None
    failure_class: str | None = None
    selected_scout_finding_ids: tuple[str, ...] = ()
    selected_scout_finding_count: int = 0
    source_refs: tuple[str, ...] = ()
    constructor_provenance: dict[str, Any] | None = None
    error_code: str | None = None
    trigger: str = "manual"
    routine_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_refs"] = list(self.source_refs)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DailyBriefRun":
        refs = value.get("source_refs", [])
        selected_ids = value.get("selected_scout_finding_ids", [])
        if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
            raise ValueError("invalid_daily_brief_history")
        if not isinstance(selected_ids, list) or any(not isinstance(item, str) for item in selected_ids):
            raise ValueError("invalid_daily_brief_history")
        constructor_provenance = value.get("constructor_provenance")
        if constructor_provenance is not None and not isinstance(constructor_provenance, dict):
            raise ValueError("invalid_daily_brief_history")
        status = value.get("status")
        if status not in {"success", "failed"}:
            raise ValueError("invalid_daily_brief_history")
        trigger = value.get("trigger", "manual")
        if trigger not in {"manual", "schedule"}:
            raise ValueError("invalid_daily_brief_history")
        return cls(
            run_id=str(value["run_id"]),
            requested_at=str(value["requested_at"]),
            completed_at=str(value["completed_at"]),
            status=status,
            summary=value.get("summary"),
            calendar_section=value.get("calendar_section"),
            email_section=value.get("email_section"),
            web_section=value.get("web_section"),
            scout_section=value.get("scout_section"),
            failure_stage=value.get("failure_stage"),
            failure_class=value.get("failure_class"),
            selected_scout_finding_ids=tuple(selected_ids),
            selected_scout_finding_count=int(value.get("selected_scout_finding_count", len(selected_ids))),
            source_refs=tuple(refs),
            constructor_provenance=constructor_provenance,
            error_code=value.get("error_code"),
            trigger=trigger,
            routine_id=value.get("routine_id"),
        )


class DailyBriefHistoryStore(Protocol):
    def save(self, run: DailyBriefRun) -> None: ...
    def list(self, limit: int = 50) -> list[DailyBriefRun]: ...
    def get(self, run_id: str) -> DailyBriefRun | None: ...


class InMemoryDailyBriefHistoryStore:
    def __init__(self) -> None:
        self._runs: dict[str, DailyBriefRun] = {}
        self._lock = threading.Lock()

    def save(self, run: DailyBriefRun) -> None:
        with self._lock:
            self._runs[run.run_id] = run

    def list(self, limit: int = 50) -> list[DailyBriefRun]:
        with self._lock:
            return sorted(self._runs.values(), key=lambda item: item.completed_at, reverse=True)[:limit]

    def get(self, run_id: str) -> DailyBriefRun | None:
        with self._lock:
            return self._runs.get(run_id)


class JsonDailyBriefHistoryStore:
    """User-local product history with atomic replacement on every write."""

    def __init__(self, path: Path | None = None) -> None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
        self._path = path or (root / "IAOS" / "daily-brief-history.json")
        self._lock = threading.Lock()

    def save(self, run: DailyBriefRun) -> None:
        with self._lock:
            runs = self._read()
            runs[run.run_id] = run
            self._write(runs)

    def list(self, limit: int = 50) -> list[DailyBriefRun]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("invalid_history_limit")
        with self._lock:
            return sorted(self._read().values(), key=lambda item: item.completed_at, reverse=True)[:limit]

    def get(self, run_id: str) -> DailyBriefRun | None:
        if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
            raise ValueError("invalid_run_id")
        with self._lock:
            return self._read().get(run_id)

    def _read(self) -> dict[str, DailyBriefRun]:
        if not self._path.exists():
            return {}
        value = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("invalid_daily_brief_history")
        runs = {}
        for item in value:
            run = DailyBriefRun.from_dict(item)
            runs[run.run_id] = run
        return runs

    def _write(self, runs: dict[str, DailyBriefRun]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="daily-brief-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump([run.to_dict() for run in runs.values()], stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def new_run_id() -> str:
    return "brief-" + uuid.uuid4().hex


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
