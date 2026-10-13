from application.daily_brief_api import DailyBriefApi
from application.daily_brief import DailyBriefFailure
from application.daily_brief_history import InMemoryDailyBriefHistoryStore
from application.scouts import InMemoryScoutHistoryStore, InMemoryScoutStore, ScoutService
from cognitive.contracts import DailyBriefResult
from governance.aep_executors import FakeWebSearchExecutor


def payload():
    return {"title": "AI", "query": "artificial intelligence", "allowed_domains": ["example.com"], "enabled": False, "time": "08:00", "timezone": "UTC"}


def request_payload():
    return {"timezone": "UTC", "calendar_window": {"after": "a", "before": "b"}, "email_window": {"after": "a", "before": "b"}, "email_limit": 1, "web_queries": ["q"], "web_result_limit": 1}


class Workflow:
    def __init__(self, result, selected=(), error=None):
        self.result, self.last_scout_findings, self.error = result, tuple(selected), error

    def run(self, request, proposal_id):
        if self.error:
            raise self.error
        return self.result


def result(scout_section="finding"):
    return DailyBriefResult("2026-10-06T00:00:00+00:00", "summary", "agenda", "email", "web", ("calendar.read:1", "email.read:1", "web.search:1", "web.search:scout"), scout_section)


def test_success_persists_brief_then_marks_exact_findings_consumed():
    scout_history = InMemoryScoutHistoryStore()
    scouts = ScoutService(InMemoryScoutStore(), scout_history, executor=FakeWebSearchExecutor())
    scout = scouts.save(payload())
    finding_run = scouts.run(scout.scout_id)
    selected = scouts.unconsumed_findings()
    assert len(selected) == 1
    workflow = Workflow(result(), selected)
    history = InMemoryDailyBriefHistoryStore()
    view = DailyBriefApi(workflow, history, scouts).run(request_payload())
    assert view["status"] == "success"
    assert history.get(view["run_id"]).scout_section == "finding"
    assert scouts.unconsumed_findings() == []
    assert scout_history.list(scout.scout_id)[0].run_id == finding_run.run_id


def test_failed_brief_leaves_findings_unconsumed():
    scouts = ScoutService(InMemoryScoutStore(), InMemoryScoutHistoryStore(), executor=FakeWebSearchExecutor())
    scout = scouts.save(payload())
    scouts.run(scout.scout_id)
    selected = scouts.unconsumed_findings()
    history = InMemoryDailyBriefHistoryStore()
    api = DailyBriefApi(Workflow(result(), selected, RuntimeError("synthesis_provider_invalid")), history, scouts)
    try:
        api.run(request_payload())
        assert False, "expected failure"
    except RuntimeError:
        pass
    assert len(scouts.unconsumed_findings()) == 1
    assert history.list()[0].status == "failed"


def test_consumed_and_failed_scout_findings_are_not_selected():
    scouts = ScoutService(InMemoryScoutStore(), InMemoryScoutHistoryStore(), executor=FakeWebSearchExecutor())
    scout = scouts.save(payload())
    scouts.run(scout.scout_id)
    selected = scouts.unconsumed_findings()
    scouts.mark_consumed(selected, "2026-10-06T00:00:00+00:00")
    assert scouts.unconsumed_findings() == []


def test_failure_provenance_preserves_first_stage_and_selected_ids():
    scouts = ScoutService(InMemoryScoutStore(), InMemoryScoutHistoryStore(), executor=FakeWebSearchExecutor())
    scout = scouts.save(payload())
    scouts.run(scout.scout_id)
    selected = scouts.unconsumed_findings()
    for stage in ("scout_selection", "calendar_read", "email_read", "web_search", "agnes_synthesis", "result_construction"):
        history = InMemoryDailyBriefHistoryStore()
        api = DailyBriefApi(Workflow(result(), selected, DailyBriefFailure(stage, "injected_failure")), history, scouts)
        try:
            api.run(request_payload())
        except DailyBriefFailure:
            pass
        failed = history.list()[0]
        assert failed.failure_stage == stage
        assert failed.failure_class == "injected_failure"
        assert failed.selected_scout_finding_ids == (selected[0]["finding_id"],)
        assert failed.selected_scout_finding_count == 1
        assert scouts.unconsumed_findings()
