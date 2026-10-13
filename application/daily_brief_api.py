"""Thin product adapter for the already-governed Daily Brief workflow."""
from __future__ import annotations

from typing import Any, Mapping

import logging

from application.daily_brief import DailyBriefFailure, DailyBriefWorkflow
from application.daily_brief_history import DailyBriefHistoryStore, DailyBriefRun, new_run_id, now_utc
from cognitive.contracts import DailyBriefRequest

_LOGGER = logging.getLogger(__name__)


class DailyBriefApi:
    def __init__(self, workflow: DailyBriefWorkflow, history: DailyBriefHistoryStore, scout_service=None) -> None:
        self._workflow = workflow
        self._history = history
        self._scout_service = scout_service

    def run(self, payload: Mapping[str, Any], *, trigger: str = "manual", routine_id: str | None = None) -> dict[str, Any]:
        if trigger not in {"manual", "schedule"}:
            raise ValueError("invalid_trigger")
        if trigger == "schedule" and not routine_id:
            raise ValueError("routine_id_required")
        requested_at = now_utc()
        run_id = new_run_id()
        request = DailyBriefRequest(
            timezone=self._text(payload, "timezone"),
            calendar_window=self._window(payload, "calendar_window"),
            email_window=self._window(payload, "email_window"),
            email_limit=self._integer(payload, "email_limit"),
            web_queries=tuple(self._queries(payload.get("web_queries"))),
            web_result_limit=self._integer(payload, "web_result_limit"),
        )
        try:
            result = self._workflow.run(request, proposal_id="daily-brief-ui-run")
        except Exception as exc:
            failure = exc if isinstance(exc, DailyBriefFailure) else DailyBriefFailure("unknown", "unclassified_failure", exc)
            failed = DailyBriefRun(
                run_id=run_id,
                requested_at=requested_at,
                completed_at=now_utc(),
                status="failed",
                error_code=_safe_error_code(exc),
                trigger=trigger,
                routine_id=routine_id,
                failure_stage=failure.stage,
                failure_class=failure.failure_class,
                selected_scout_finding_ids=tuple(str(item["finding_id"]) for item in getattr(self._workflow, "last_scout_findings", ())),
                selected_scout_finding_count=len(getattr(self._workflow, "last_scout_findings", ())),
                constructor_provenance=failure.diagnostics.get("constructor"),
            )
            try:
                self._history.save(failed)
            except Exception:
                _LOGGER.exception("daily_brief_history_persistence_failed stage=history_persistence")
            raise
        completed_at = now_utc()
        selected_findings = tuple(getattr(self._workflow, "last_scout_findings", ()))
        run = DailyBriefRun(
            run_id=run_id,
            requested_at=requested_at,
            completed_at=completed_at,
            status="success",
            summary=result.summary,
            calendar_section=result.calendar_section,
            email_section=result.email_section,
            web_section=result.web_section,
            scout_section=result.scout_section,
            source_refs=tuple(result.source_refs),
            trigger=trigger,
            routine_id=routine_id,
            selected_scout_finding_ids=tuple(str(item["finding_id"]) for item in selected_findings),
            selected_scout_finding_count=len(selected_findings),
        )
        try:
            self._history.save(run)
        except Exception as exc:
            _LOGGER.exception("daily_brief_history_persistence_failed stage=history_persistence")
            raise DailyBriefFailure("history_persistence", "history_save_failed", exc) from exc
        if self._scout_service is not None:
            try:
                self._scout_service.mark_consumed(list(self._workflow.last_scout_findings), completed_at)
            except Exception as exc:
                _LOGGER.exception("daily_brief_scout_consumption_failed stage=scout_consumption")
                raise DailyBriefFailure("scout_consumption", "consumption_failed", exc) from exc
        return _run_view(run)

    def list_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return [_run_view(run, detail=False) for run in self._history.list(limit)]

    def get_history(self, run_id: str) -> dict[str, Any] | None:
        run = self._history.get(run_id)
        return _run_view(run) if run is not None else None

    @staticmethod
    def _text(payload: Mapping[str, Any], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"invalid:{name}")
        return value

    @classmethod
    def _window(cls, payload: Mapping[str, Any], name: str) -> dict[str, str]:
        value = payload.get(name)
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid:{name}")
        return {"after": cls._text(value, "after"), "before": cls._text(value, "before")}

    @staticmethod
    def _integer(payload: Mapping[str, Any], name: str) -> int:
        value = payload.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"invalid:{name}")
        return value

    @staticmethod
    def _queries(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("invalid:web_queries")
        return list(value)


def _safe_error_code(error: Exception) -> str:
    code = str(error).strip()
    allowed = {
        "google_connection_required",
        "calendar_read_failed",
        "email_read_failed",
        "web_search_failed",
        "synthesis_provider_not_configured",
        "synthesis_provider_invalid",
        "synthesis_contract_invalid",
        "daily_brief_v1_requires_one_web_query",
    }
    return code if code in allowed else "daily_brief_failed"


def _run_view(run: DailyBriefRun, detail: bool = True) -> dict[str, Any]:
    value: dict[str, Any] = {
        "run_id": run.run_id,
        "generated_at": run.completed_at,
        "requested_at": run.requested_at,
        "completed_at": run.completed_at,
        "status": run.status,
        "error_code": run.error_code,
        "trigger": run.trigger,
        "routine_id": run.routine_id,
    }
    if detail:
        value.update({
            "summary": run.summary,
            "calendar_section": run.calendar_section,
            "email_section": run.email_section,
            "web_section": run.web_section,
            "scout_section": run.scout_section,
        "failure_stage": run.failure_stage,
        "failure_class": run.failure_class,
        "selected_scout_finding_ids": list(run.selected_scout_finding_ids),
        "selected_scout_finding_count": run.selected_scout_finding_count,
            "source_refs": list(run.source_refs),
        })
    return value
