"""One explicit daily Daily Brief routine; not a general automation engine."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DailyBriefRoutine:
    routine_id: str
    frequency: str
    time: str
    timezone: str
    enabled: bool
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DailyBriefRoutine":
        routine = cls(
            routine_id=str(value["routine_id"]),
            frequency=str(value["frequency"]),
            time=str(value["time"]),
            timezone=str(value["timezone"]),
            enabled=bool(value["enabled"]),
            updated_at=str(value["updated_at"]),
        )
        _validate(routine.time, routine.timezone)
        if routine.frequency != "daily":
            raise ValueError("unsupported_routine_frequency")
        return routine


class DailyBriefRoutineStore(Protocol):
    def save(self, routine: DailyBriefRoutine) -> None: ...
    def get(self) -> DailyBriefRoutine | None: ...


class InMemoryDailyBriefRoutineStore:
    def __init__(self) -> None:
        self._routine: DailyBriefRoutine | None = None

    def save(self, routine: DailyBriefRoutine) -> None:
        self._routine = routine

    def get(self) -> DailyBriefRoutine | None:
        return self._routine


class JsonDailyBriefRoutineStore:
    def __init__(self, path: Path | None = None) -> None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
        self._path = path or (root / "IAOS" / "daily-brief-routine.json")
        self._lock = threading.Lock()

    def save(self, routine: DailyBriefRoutine) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix="daily-routine-", suffix=".tmp", dir=self._path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(routine.to_dict(), stream, ensure_ascii=False, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def get(self) -> DailyBriefRoutine | None:
        with self._lock:
            if not self._path.exists():
                return None
            return DailyBriefRoutine.from_dict(json.loads(self._path.read_text(encoding="utf-8")))


class DailyBriefRoutineService:
    """Schedules only the single Daily Brief workflow using a one-shot Timer."""

    def __init__(self, runner, store: DailyBriefRoutineStore, timer_factory=threading.Timer) -> None:
        self._runner = runner
        self._store = store
        self._timer_factory = timer_factory
        self._timer = None
        self._lock = threading.Lock()

    def start(self) -> None:
        routine = self._store.get()
        if routine and routine.enabled:
            self._arm(routine)

    def configure(self, schedule_time: str, timezone_name: str, enabled: bool) -> DailyBriefRoutine:
        _validate(schedule_time, timezone_name)
        routine = DailyBriefRoutine("daily-brief", "daily", schedule_time, timezone_name, bool(enabled), _now())
        self._store.save(routine)
        self._cancel_timer()
        if routine.enabled:
            self._arm(routine)
        return routine

    def get(self) -> DailyBriefRoutine | None:
        return self._store.get()

    def fire_now(self) -> bool:
        routine = self._store.get()
        if routine is None or not routine.enabled:
            return False
        self._runner.run(_payload_for_today(routine.timezone), trigger="schedule", routine_id=routine.routine_id)
        return True

    def close(self) -> None:
        self._cancel_timer()

    def _arm(self, routine: DailyBriefRoutine) -> None:
        delay = max(0.0, (_next_occurrence(routine) - datetime.now(timezone.utc)).total_seconds())
        timer = self._timer_factory(delay, self._fire_scheduled)
        timer.daemon = True
        with self._lock:
            self._timer = timer
        timer.start()

    def _fire_scheduled(self) -> None:
        routine = self._store.get()
        if routine is None or not routine.enabled:
            return
        try:
            self._runner.run(_payload_for_today(routine.timezone), trigger="schedule", routine_id=routine.routine_id)
        except Exception:
            # DailyBriefApi records the failed run; the timer must not turn a
            # provider failure into an unhandled background-thread exception.
            pass
        finally:
            current = self._store.get()
            if current and current.enabled:
                self._arm(current)

    def _cancel_timer(self) -> None:
        with self._lock:
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()


def _validate(schedule_time: str, timezone_name: str) -> None:
    if not isinstance(schedule_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
        raise ValueError("invalid_routine_time")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("invalid_routine_timezone")
    try:
        _zone(timezone_name)
    except Exception as exc:
        raise ValueError("invalid_routine_timezone") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_occurrence(routine: DailyBriefRoutine) -> datetime:
    zone = _zone(routine.timezone)
    local_now = datetime.now(zone)
    hour, minute = (int(value) for value in routine.time.split(":"))
    candidate = datetime.combine(local_now.date(), time(hour, minute), tzinfo=zone)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _payload_for_today(timezone_name: str) -> dict[str, Any]:
    zone = _zone(timezone_name)
    today = datetime.now(zone).date()
    start = datetime.combine(today, time.min, tzinfo=zone)
    end = start + timedelta(days=1)
    return {
        "timezone": timezone_name,
        "calendar_window": {"after": start.isoformat(), "before": end.isoformat()},
        "email_window": {"after": today.isoformat(), "before": end.date().isoformat()},
        "email_limit": 1,
        "web_queries": ["artificial intelligence"],
        "web_result_limit": 1,
    }


def _zone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        # The Windows runtime used by IAOS may not ship tzdata. These are the
        # explicit V1 zones supported without silently guessing a timezone.
        fixed = {
            "UTC": timezone.utc,
            "America/Sao_Paulo": timezone(timedelta(hours=-3)),
        }
        if timezone_name in fixed:
            return fixed[timezone_name]
        raise ValueError("invalid_routine_timezone")
