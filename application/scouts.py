"""V1A: persistent, read-only, governed web Scouts."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

from application.external_read_composition import product_to_governance_proposal
from governance.aep_adapter import AEPAdapter
from governance.aep_executors import AEPExecutorRegistry, BraveSearchExecutor
from governance.aep_runtime import AEPRuntime
from governance.approval import ApprovalGate
from governance.auth_registry import create_authorization_registry
from governance.consumption import create_consumption_ledger
from governance.evidence import InMemoryEvidenceStore
from governance.external_read import ExternalReadAuthorizer, ExternalReadDescriptor
from governance.handle_binding import BindingTable
from cognitive.agent import DEFAULT_ASSISTANT
from cognitive.contracts import Proposal, Provenance


@dataclass(frozen=True)
class Scout:
    scout_id: str
    title: str
    query: str
    allowed_domains: tuple[str, ...]
    enabled: bool
    time: str
    timezone: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["allowed_domains"] = list(self.allowed_domains)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Scout":
        domains = value.get("allowed_domains")
        if not isinstance(domains, list) or any(not isinstance(item, str) for item in domains):
            raise ValueError("invalid_scout")
        scout = cls(
            scout_id=str(value["scout_id"]), title=str(value["title"]), query=str(value["query"]),
            allowed_domains=tuple(domains), enabled=bool(value["enabled"]), time=str(value["time"]),
            timezone=str(value["timezone"]), updated_at=str(value["updated_at"]),
        )
        _validate_scout_fields(scout.title, scout.query, scout.allowed_domains, scout.time, scout.timezone)
        return scout


@dataclass(frozen=True)
class ScoutRun:
    run_id: str
    scout_id: str
    started_at: str
    completed_at: str
    status: str
    findings: tuple[Mapping[str, Any], ...] = ()
    source_refs: tuple[str, ...] = ()
    error: str | None = None
    trigger: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["findings"] = [dict(item) for item in self.findings]
        value["source_refs"] = list(self.source_refs)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScoutRun":
        findings = value.get("findings", [])
        refs = value.get("source_refs", [])
        if not isinstance(findings, list) or not all(isinstance(item, dict) for item in findings):
            raise ValueError("invalid_scout_history")
        if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
            raise ValueError("invalid_scout_history")
        if value.get("status") not in {"success", "failed"} or value.get("trigger", "manual") not in {"manual", "schedule"}:
            raise ValueError("invalid_scout_history")
        return cls(
            run_id=str(value["run_id"]), scout_id=str(value["scout_id"]),
            started_at=str(value["started_at"]), completed_at=str(value["completed_at"]),
            status=str(value["status"]), findings=tuple(findings), source_refs=tuple(refs),
            error=value.get("error"), trigger=value.get("trigger", "manual"),
        )


class ScoutStore(Protocol):
    def list(self) -> list[Scout]: ...
    def get(self, scout_id: str) -> Scout | None: ...
    def save(self, scout: Scout) -> None: ...
    def delete(self, scout_id: str) -> None: ...


class ScoutHistoryStore(Protocol):
    def save(self, run: ScoutRun) -> None: ...
    def list(self, scout_id: str | None = None, limit: int = 100) -> list[ScoutRun]: ...
    def mark_findings_consumed(self, run_id: str, finding_ids: list[str], consumed_at: str) -> None: ...


class InMemoryScoutStore:
    def __init__(self) -> None:
        self._items: dict[str, Scout] = {}

    def list(self) -> list[Scout]:
        return list(self._items.values())

    def get(self, scout_id: str) -> Scout | None:
        return self._items.get(scout_id)

    def save(self, scout: Scout) -> None:
        self._items[scout.scout_id] = scout

    def delete(self, scout_id: str) -> None:
        self._items.pop(scout_id, None)


class InMemoryScoutHistoryStore:
    def __init__(self) -> None:
        self._items: list[ScoutRun] = []

    def save(self, run: ScoutRun) -> None:
        self._items = [item for item in self._items if item.run_id != run.run_id] + [run]

    def list(self, scout_id: str | None = None, limit: int = 100) -> list[ScoutRun]:
        items = [item for item in self._items if scout_id is None or item.scout_id == scout_id]
        return sorted(items, key=lambda item: item.completed_at, reverse=True)[:limit]

    def mark_findings_consumed(self, run_id: str, finding_ids: list[str], consumed_at: str) -> None:
        for index, run in enumerate(self._items):
            if run.run_id == run_id:
                findings = tuple({**item, "consumed_at": consumed_at} if item.get("finding_id") in finding_ids else item for item in run.findings)
                self._items[index] = ScoutRun(run.run_id, run.scout_id, run.started_at, run.completed_at, run.status, findings, run.source_refs, run.error, run.trigger)


class JsonScoutStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _app_data_path("scouts.json")
        self._lock = threading.RLock()

    def list(self) -> list[Scout]:
        with self._lock:
            return [Scout.from_dict(item) for item in self._read()]

    def get(self, scout_id: str) -> Scout | None:
        return next((item for item in self.list() if item.scout_id == scout_id), None)

    def save(self, scout: Scout) -> None:
        with self._lock:
            items = [item for item in self._read() if item.get("scout_id") != scout.scout_id]
            items.append(scout.to_dict())
            self._write(items)

    def delete(self, scout_id: str) -> None:
        with self._lock:
            self._write([item for item in self._read() if item.get("scout_id") != scout_id])

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        value = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("invalid_scout_store")
        return value

    def _write(self, items: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="scouts-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(items, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class JsonScoutHistoryStore(InMemoryScoutHistoryStore):
    """Small JSON product store; no evidence or token material is written."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _app_data_path("scout-history.json")
        self._lock = threading.RLock()

    def save(self, run: ScoutRun) -> None:
        with self._lock:
            items = [item for item in self._read() if item.get("run_id") != run.run_id]
            items.append(run.to_dict())
            self._write(items)

    def list(self, scout_id: str | None = None, limit: int = 100) -> list[ScoutRun]:
        with self._lock:
            runs = [ScoutRun.from_dict(item) for item in self._read()]
        if scout_id is not None:
            runs = [item for item in runs if item.scout_id == scout_id]
        return sorted(runs, key=lambda item: item.completed_at, reverse=True)[:limit]

    def mark_findings_consumed(self, run_id: str, finding_ids: list[str], consumed_at: str) -> None:
        with self._lock:
            runs = [ScoutRun.from_dict(item) for item in self._read()]
            updated = []
            for run in runs:
                if run.run_id == run_id:
                    findings = tuple({**item, "consumed_at": consumed_at} if item.get("finding_id") in finding_ids else item for item in run.findings)
                    run = ScoutRun(run.run_id, run.scout_id, run.started_at, run.completed_at, run.status, findings, run.source_refs, run.error, run.trigger)
                updated.append(run.to_dict())
            self._write(updated)

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        value = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("invalid_scout_history")
        return value

    def _write(self, items: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="scout-history-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(items, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class ScoutService:
    def __init__(self, store: ScoutStore, history: ScoutHistoryStore, executor=None, timer_factory=threading.Timer) -> None:
        self._store = store
        self._history = history
        self._executor = executor or BraveSearchExecutor()
        self._timer_factory = timer_factory
        self._timers: dict[str, Any] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        for scout in self._store.list():
            if scout.enabled:
                self._arm(scout)

    def list(self) -> list[Scout]:
        return self._store.list()

    def get(self, scout_id: str) -> Scout | None:
        return self._store.get(scout_id)

    def save(self, payload: Mapping[str, Any]) -> Scout:
        scout_id = payload.get("scout_id") or "scout-" + uuid.uuid4().hex
        if not isinstance(scout_id, str) or not scout_id:
            raise ValueError("invalid_scout_id")
        scout = Scout(
            scout_id=scout_id,
            title=payload.get("title"), query=payload.get("query"),
            allowed_domains=tuple(payload.get("allowed_domains", ())),
            enabled=payload.get("enabled", False), time=payload.get("time"),
            timezone=payload.get("timezone"), updated_at=_now(),
        )
        _validate_scout_fields(scout.title, scout.query, scout.allowed_domains, scout.time, scout.timezone)
        self._store.save(scout)
        self._cancel(scout.scout_id)
        if scout.enabled:
            self._arm(scout)
        return scout

    def delete(self, scout_id: str) -> None:
        if self._store.get(scout_id) is None:
            raise KeyError("scout_not_found")
        self._cancel(scout_id)
        self._store.delete(scout_id)

    def history(self, scout_id: str | None = None) -> list[ScoutRun]:
        return self._history.list(scout_id)

    def unconsumed_findings(self, limit: int = 20) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for run in self._history.list(limit=1000):
            if run.status != "success":
                continue
            for finding in run.findings:
                if finding.get("finding_id") and finding.get("consumed_at") is None:
                    selected.append({
                        "scout_id": run.scout_id, "scout_run_id": run.run_id,
                        "finding_id": finding["finding_id"],
                        "content": {key: value for key, value in finding.items() if key not in {"finding_id", "scout_id", "scout_run_id", "consumed_at"}},
                        "source_refs": list(run.source_refs),
                    })
                if len(selected) >= limit:
                    return selected
        return selected

    def mark_consumed(self, findings: list[Mapping[str, Any]], consumed_at: str) -> None:
        grouped: dict[str, list[str]] = {}
        for finding in findings:
            grouped.setdefault(str(finding["scout_run_id"]), []).append(str(finding["finding_id"]))
        for run_id, ids in grouped.items():
            self._history.mark_findings_consumed(run_id, ids, consumed_at)

    def run(self, scout_id: str, trigger: str = "manual") -> ScoutRun:
        if trigger not in {"manual", "schedule"}:
            raise ValueError("invalid_trigger")
        scout = self._store.get(scout_id)
        if scout is None:
            raise KeyError("scout_not_found")
        if trigger == "schedule" and not scout.enabled:
            raise ValueError("scout_disabled")
        started = _now()
        run_id = "scout-run-" + uuid.uuid4().hex
        try:
            content, source_ref = self._governed_search(scout, run_id)
            findings = tuple(_finding(item, scout.scout_id, run_id, index) for index, item in enumerate(content.get("results", [])) if isinstance(item, Mapping))
            run = ScoutRun(run_id, scout.scout_id, started, _now(), "success", findings, (source_ref,), None, trigger)
        except Exception as exc:
            run = ScoutRun(run_id, scout.scout_id, started, _now(), "failed", (), (), _safe_error(str(exc)), trigger)
        self._history.save(run)
        if run.status == "failed":
            raise RuntimeError(run.error or "scout_failed")
        return run

    def close(self) -> None:
        for scout_id in list(self._timers):
            self._cancel(scout_id)

    def _governed_search(self, scout: Scout, run_id: str) -> tuple[Mapping[str, Any], str]:
        descriptor = ExternalReadDescriptor("web.search", {
            "provider_ref": "brave.search.v1", "allowed_domains": list(scout.allowed_domains),
        }, {
            "requested_domains": list(scout.allowed_domains), "query": scout.query, "result_limit": 10,
        })
        product = Proposal("scout-" + scout.scout_id + "-" + run_id, "run scout", DEFAULT_ASSISTANT.assistant_id, Provenance("user", "scout-v1"))
        governance_proposal = product_to_governance_proposal(product, descriptor)
        evidence = InMemoryEvidenceStore()
        issuer, view = create_authorization_registry()
        _ledger, handle = create_consumption_ledger()
        runtime = AEPRuntime(AEPExecutorRegistry({"web.search": self._executor}), BindingTable(), view, handle)
        adapter = AEPAdapter(runtime)
        approval = ApprovalGate()
        authorizer = ExternalReadAuthorizer(evidence, issuer, approval)
        canonical = authorizer.canonicalize(governance_proposal)
        approval.grant("scout", governance_proposal.intent_id, canonical.digest())
        action = authorizer.authorize(governance_proposal)
        result = adapter.execute(action, evidence)
        if not result.ok or not isinstance(result.structured_output, Mapping):
            raise RuntimeError("web_search_failed")
        if result.result_authority != "DATA_ONLY" or result.result_provenance != "TOOL_RESULT":
            raise RuntimeError("result_boundary_failed")
        return dict(result.structured_output), f"web.search:{result.execution_id}"

    def _arm(self, scout: Scout) -> None:
        delay = max(0.0, (_next_occurrence(scout) - datetime.now(timezone.utc)).total_seconds())
        timer = self._timer_factory(delay, lambda: self._scheduled(scout.scout_id))
        timer.daemon = True
        with self._lock:
            self._timers[scout.scout_id] = timer
        timer.start()

    def _scheduled(self, scout_id: str) -> None:
        scout = self._store.get(scout_id)
        if scout is None or not scout.enabled:
            return
        try:
            self.run(scout_id, "schedule")
        except Exception:
            pass
        current = self._store.get(scout_id)
        if current and current.enabled:
            self._arm(current)

    def _cancel(self, scout_id: str) -> None:
        with self._lock:
            timer = self._timers.pop(scout_id, None)
        if timer is not None:
            timer.cancel()


def _finding(item: Mapping[str, Any], scout_id: str, run_id: str, index: int) -> dict[str, Any]:
    return {"finding_id": f"{run_id}-finding-{index + 1}", "scout_id": scout_id, "scout_run_id": run_id, "consumed_at": None, **{key: item.get(key) for key in ("title", "url", "description", "age") if key in item}}


def _safe_error(error: str) -> str:
    allowed = {"web_search_failed", "result_boundary_failed", "credential_absent", "provider_request_rejected", "provider_unavailable", "provider_rate_limit", "result_domain_out_of_scope"}
    return error if error in allowed else "scout_failed"


def _validate_scout_fields(title: Any, query: Any, domains: Any, schedule_time: Any, timezone_name: Any) -> None:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("invalid_scout_title")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("invalid_scout_query")
    if not isinstance(domains, (tuple, list)) or not domains or any(not isinstance(item, str) or not item.strip() or "://" in item for item in domains):
        raise ValueError("allowed_domains_required")
    if not isinstance(schedule_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
        raise ValueError("invalid_scout_time")
    _zone(timezone_name)


def _zone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        fixed = {"UTC": timezone.utc, "America/Sao_Paulo": timezone(timedelta(hours=-3))}
        if timezone_name in fixed:
            return fixed[timezone_name]
        raise ValueError("invalid_scout_timezone")


def _next_occurrence(scout: Scout) -> datetime:
    zone = _zone(scout.timezone)
    local_now = datetime.now(zone)
    hour, minute = (int(value) for value in scout.time.split(":"))
    candidate = datetime.combine(local_now.date(), time(hour, minute), tzinfo=zone)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _app_data_path(name: str) -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "IAOS"
    return root / name
