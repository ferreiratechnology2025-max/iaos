from __future__ import annotations

import json

from application.daily_brief_history import DailyBriefRun, InMemoryDailyBriefHistoryStore, JsonDailyBriefHistoryStore
from application.daily_brief_api import DailyBriefApi
from application.daily_brief import DailyBriefFailure, _constructor_validation_metadata
from cognitive.contracts import DailyBriefResult


def _run(run_id: str, status: str = "success") -> DailyBriefRun:
    return DailyBriefRun(
        run_id=run_id,
        requested_at=f"2026-10-06T00:00:0{run_id[-1]}+00:00",
        completed_at=f"2026-10-06T00:00:1{run_id[-1]}+00:00",
        status=status,
        summary="brief" if status == "success" else None,
        calendar_section="agenda" if status == "success" else None,
        email_section="email" if status == "success" else None,
        web_section="web" if status == "success" else None,
        source_refs=("calendar.read:run", "email.read:run", "web.search:run") if status == "success" else (),
        error_code=None if status == "success" else "email_read_failed",
    )


def test_history_persists_success_and_failure_across_store_instances(tmp_path):
    path = tmp_path / "daily-brief-history.json"
    first = JsonDailyBriefHistoryStore(path)
    first.save(_run("brief-1"))
    first.save(_run("brief-2", "failed"))

    second = JsonDailyBriefHistoryStore(path)
    runs = second.list()
    assert [item.run_id for item in runs] == ["brief-2", "brief-1"]
    assert second.get("brief-1").source_refs == ("calendar.read:run", "email.read:run", "web.search:run")
    assert second.get("brief-2").error_code == "email_read_failed"


def test_history_save_replaces_same_run_only_and_keeps_other_runs():
    store = InMemoryDailyBriefHistoryStore()
    store.save(_run("brief-1"))
    store.save(_run("brief-2"))
    store.save(_run("brief-1", "failed"))
    assert [item.run_id for item in store.list()] == ["brief-2", "brief-1"]
    assert store.get("brief-1").status == "failed"


def test_history_file_contains_product_state_not_evidence_or_secrets(tmp_path):
    path = tmp_path / "history.json"
    JsonDailyBriefHistoryStore(path).save(_run("brief-1"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw[0]) == {
        "run_id", "requested_at", "completed_at", "status", "summary",
        "calendar_section", "email_section", "web_section", "source_refs", "error_code",
        "scout_section", "trigger", "routine_id",
        "failure_stage", "failure_class", "selected_scout_finding_ids", "selected_scout_finding_count",
        "constructor_provenance",
    }
    assert "audit" not in json.dumps(raw).lower()
    assert "token" not in json.dumps(raw).lower()


class _Workflow:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def run(self, request, proposal_id):
        if self.error:
            raise self.error
        return self.result


def test_api_persists_successful_run_and_returns_run_id():
    result = DailyBriefResult("2026-10-06T00:00:00+00:00", "summary", "agenda", "email", "web", ("calendar.read:1", "email.read:1", "web.search:1"))
    store = InMemoryDailyBriefHistoryStore()
    api = DailyBriefApi(_Workflow(result=result), store)
    view = api.run({
        "timezone": "UTC",
        "calendar_window": {"after": "a", "before": "b"},
        "email_window": {"after": "a", "before": "b"},
        "email_limit": 1,
        "web_queries": ["query"],
        "web_result_limit": 1,
    })
    assert view["status"] == "success"
    assert view["generated_at"] == view["completed_at"]
    assert store.get(view["run_id"]).summary == "summary"
    assert store.get(view["run_id"]).source_refs == ("calendar.read:1", "email.read:1", "web.search:1")
    scheduled = api.run({
        "timezone": "UTC",
        "calendar_window": {"after": "a", "before": "b"},
        "email_window": {"after": "a", "before": "b"},
        "email_limit": 1,
        "web_queries": ["query"],
        "web_result_limit": 1,
    }, trigger="schedule", routine_id="daily-brief")
    assert scheduled["trigger"] == "schedule"
    assert scheduled["routine_id"] == "daily-brief"


def test_api_persists_failed_run_without_partial_content():
    store = InMemoryDailyBriefHistoryStore()
    api = DailyBriefApi(_Workflow(error=RuntimeError("email_read_failed")), store)
    try:
        api.run({
            "timezone": "UTC",
            "calendar_window": {"after": "a", "before": "b"},
            "email_window": {"after": "a", "before": "b"},
            "email_limit": 1,
            "web_queries": ["query"],
            "web_result_limit": 1,
        })
        assert False, "failed Daily Brief must surface failure"
    except RuntimeError:
        pass
    run = store.list()[0]
    assert run.status == "failed"
    assert run.error_code == "email_read_failed"
    assert run.summary is None and run.source_refs == ()


def test_constructor_provenance_is_redacted_and_persisted():
    diagnostic = {
        "constructor_entered": True,
        "top_level_fields": ("generated_at", "summary", "source_refs"),
        "field_types": {"generated_at": "str", "summary": "str", "source_refs": "tuple"},
        "source_refs_count": 3,
        "scout_finding_count": 9,
        "scout_section_present": True,
        "generated_at_present": True,
        "generated_at_type": "str",
        "summary_present": True,
        "constructor_input_shape_sha256": "a" * 64,
        "constructor_failure": {
            "exception_class": "ValueError",
            "validation_path": "source_refs",
            "validation_code": "invalid",
        },
    }
    history = InMemoryDailyBriefHistoryStore()
    api = DailyBriefApi(
        _Workflow(error=DailyBriefFailure(
            "result_construction", "invalid_result",
            ValueError("invalid:source_refs containing private content"),
            diagnostics={"constructor": diagnostic},
        )),
        history,
    )
    try:
        api.run({
            "timezone": "UTC",
            "calendar_window": {"after": "a", "before": "b"},
            "email_window": {"after": "a", "before": "b"},
            "email_limit": 1,
            "web_queries": ["query"],
            "web_result_limit": 1,
        })
        assert False, "failed Daily Brief must surface failure"
    except DailyBriefFailure:
        pass
    persisted = history.list()[0].constructor_provenance
    assert persisted == diagnostic
    assert "private content" not in json.dumps(persisted)


def test_constructor_validation_metadata_is_stable_for_injected_failures():
    assert _constructor_validation_metadata(ValueError("invalid:summary")) == {
        "exception_class": "ValueError",
        "validation_path": "summary",
        "validation_code": "invalid",
    }
    assert _constructor_validation_metadata(TypeError("wrong type with private content")) == {
        "exception_class": "TypeError",
        "validation_path": None,
        "validation_code": None,
    }
    assert _constructor_validation_metadata(RuntimeError("provider payload must not persist")) == {
        "exception_class": "RuntimeError",
        "validation_path": None,
        "validation_code": None,
    }
