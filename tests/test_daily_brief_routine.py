from __future__ import annotations

import json

import pytest

from application.daily_brief_routine import (
    DailyBriefRoutineService,
    InMemoryDailyBriefRoutineStore,
    JsonDailyBriefRoutineStore,
)
from application.daily_brief_api import DailyBriefApi
from application.daily_brief_history import InMemoryDailyBriefHistoryStore
from cognitive.contracts import DailyBriefResult


class _Runner:
    def __init__(self):
        self.calls = []

    def run(self, payload, *, trigger, routine_id):
        self.calls.append((payload, trigger, routine_id))
        return {"status": "success"}


class _Workflow:
    def __init__(self, result):
        self.result = result

    def run(self, request, proposal_id):
        return self.result


def test_create_disable_and_enable_daily_routine():
    store = InMemoryDailyBriefRoutineStore()
    runner = _Runner()
    service = DailyBriefRoutineService(runner, store)
    disabled = service.configure("08:30", "America/Sao_Paulo", False)
    assert disabled.frequency == "daily"
    assert disabled.enabled is False
    assert service.fire_now() is False

    enabled = service.configure("08:30", "America/Sao_Paulo", True)
    assert enabled.enabled is True
    assert service.fire_now() is True
    assert runner.calls[0][1:] == ("schedule", "daily-brief")
    assert runner.calls[0][0]["timezone"] == "America/Sao_Paulo"
    assert runner.calls[0][0]["email_limit"] == 1
    service.close()


@pytest.mark.parametrize("schedule_time,timezone_name", [("8:30", "America/Sao_Paulo"), ("25:00", "UTC"), ("08:30", "Not/A/Timezone")])
def test_invalid_schedule_fails_closed(schedule_time, timezone_name):
    service = DailyBriefRoutineService(_Runner(), InMemoryDailyBriefRoutineStore())
    with pytest.raises(ValueError):
        service.configure(schedule_time, timezone_name, True)


def test_routine_reload_survives_and_is_single_record(tmp_path):
    path = tmp_path / "routine.json"
    first = JsonDailyBriefRoutineStore(path)
    service = DailyBriefRoutineService(_Runner(), first)
    service.configure("07:05", "UTC", True)
    service.close()
    second = JsonDailyBriefRoutineStore(path)
    routine = second.get()
    assert routine.routine_id == "daily-brief"
    assert routine.time == "07:05"
    assert routine.timezone == "UTC"
    assert json.loads(path.read_text(encoding="utf-8"))["frequency"] == "daily"


def test_scheduled_fire_uses_daily_brief_api_and_persists_schedule_trigger():
    history = InMemoryDailyBriefHistoryStore()
    api = DailyBriefApi(
        _Workflow(DailyBriefResult("now", "summary", "agenda", "email", "web", ("calendar", "email", "web"))),
        history,
    )
    service = DailyBriefRoutineService(api, InMemoryDailyBriefRoutineStore())
    service.configure("08:30", "UTC", True)
    assert service.fire_now() is True
    run = history.list()[0]
    assert run.status == "success"
    assert run.trigger == "schedule"
    assert run.routine_id == "daily-brief"
    service.close()
