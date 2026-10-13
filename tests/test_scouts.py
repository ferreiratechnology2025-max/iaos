from pathlib import Path

import pytest

from governance.aep_executors import FakeWebSearchExecutor
from application.scouts import (
    InMemoryScoutHistoryStore,
    InMemoryScoutStore,
    JsonScoutStore,
    ScoutService,
)


class FakeSearch(FakeWebSearchExecutor):
    def __init__(self, fail=False):
        super().__init__()
        self.fail = fail
        self.calls = []

    def run(self, resource, args, identity, binding=None):
        self.calls.append((resource, args))
        if self.fail:
            raise RuntimeError("provider_request_rejected")
        return super().run(resource, args, identity, binding)

    def fixture(self, resource, request):
        return {"results": [{"title": "Result", "url": "https://en.wikipedia.org/wiki/AI", "description": "Summary", "age": "today", "ignored": "no"}]}


def scout_payload(**overrides):
    value = {
        "title": "AI Scout", "query": "artificial intelligence",
        "allowed_domains": ["en.wikipedia.org"], "enabled": False,
        "time": "08:00", "timezone": "America/Sao_Paulo",
    }
    value.update(overrides)
    return value


def test_crud_enable_and_reload(tmp_path: Path):
    store = JsonScoutStore(tmp_path / "scouts.json")
    service = ScoutService(store, InMemoryScoutHistoryStore(), executor=FakeSearch())
    created = service.save(scout_payload())
    assert service.get(created.scout_id) == created
    edited = service.save({**created.to_dict(), "title": "Edited", "enabled": True})
    assert edited.title == "Edited" and edited.enabled is True
    reloaded = ScoutService(JsonScoutStore(tmp_path / "scouts.json"), InMemoryScoutHistoryStore(), executor=FakeSearch())
    assert reloaded.get(created.scout_id).title == "Edited"
    reloaded.delete(created.scout_id)
    assert reloaded.list() == []


def test_run_is_governed_and_persists_data_only_finding():
    search = FakeSearch()
    history = InMemoryScoutHistoryStore()
    service = ScoutService(InMemoryScoutStore(), history, executor=search)
    scout = service.save(scout_payload())
    run = service.run(scout.scout_id)
    assert run.status == "success"
    assert run.trigger == "manual"
    assert run.source_refs
    assert {key: run.findings[0][key] for key in ("title", "url", "description", "age")} == {
        "title": "Result", "url": "https://en.wikipedia.org/wiki/AI", "description": "Summary", "age": "today"
    }
    assert "ignored" not in run.findings[0]
    assert service.history(scout.scout_id)[0].run_id == run.run_id


def test_failed_run_is_persisted_and_schedule_requires_enabled():
    history = InMemoryScoutHistoryStore()
    service = ScoutService(InMemoryScoutStore(), history, executor=FakeSearch(fail=True))
    scout = service.save(scout_payload())
    with pytest.raises(RuntimeError, match="web_search_failed"):
        service.run(scout.scout_id)
    assert history.list(scout.scout_id)[0].status == "failed"
    with pytest.raises(ValueError, match="scout_disabled"):
        service.run(scout.scout_id, "schedule")


def test_domains_and_timezone_are_required():
    service = ScoutService(InMemoryScoutStore(), InMemoryScoutHistoryStore(), executor=FakeSearch())
    with pytest.raises(ValueError, match="allowed_domains_required"):
        service.save(scout_payload(allowed_domains=[]))
    with pytest.raises(ValueError, match="invalid_scout_timezone"):
        service.save(scout_payload(timezone="Mars/Olympus"))


def test_enabled_scout_arms_daily_schedule_and_records_schedule_trigger():
    class Timer:
        instances = []
        def __init__(self, delay, callback): self.callback = callback; self.cancelled = False; self.instances.append(self)
        def start(self): pass
        def cancel(self): self.cancelled = True

    history = InMemoryScoutHistoryStore()
    service = ScoutService(InMemoryScoutStore(), history, executor=FakeSearch(), timer_factory=Timer)
    scout = service.save(scout_payload(enabled=True))
    assert len(Timer.instances) == 1
    Timer.instances[0].callback()
    assert history.list(scout.scout_id)[0].trigger == "schedule"
    service.close()
