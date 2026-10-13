import pytest

from application.agent_loop import (
    AgentLoop,
    ConsumptionBoundaryError,
    ConsumptionBoundaryEvent,
    VerificationResult,
)
from application.source_facts import SearchOutcome
from cognitive.agent_contracts import AgentGoal

from test_agent_loop import FakeComputer, ScriptedProvider, OK


GOAL = AgentGoal("boundary", "do the thing", max_steps=2)
FINISH = '{"kind":"finish","claim":"done"}'
SEARCH = '{"kind":"search","query":"mission"}'


class SearchComputer(FakeComputer):
    def __init__(self):
        super().__init__()
        self.search_calls = []

    def search(self, step_id, query):
        self.search_calls.append((step_id, query))
        return SearchOutcome(False, "no_result")


def test_model_boundary_precedes_provider_call():
    order = []

    class Provider(ScriptedProvider):
        def complete(self, prompt):
            order.append("provider")
            return super().complete(prompt)

    def boundary(event):
        assert isinstance(event, ConsumptionBoundaryEvent)
        assert (event.kind, event.ordinal, event.turn) == ("model_turn", 1, 1)
        order.append("boundary")

    AgentLoop(Provider([FINISH]), FakeComputer(), OK,
              consumption_boundary_sink=boundary).run(GOAL)
    assert order == ["boundary", "provider"]


def test_model_boundary_sink_failure_prevents_provider_call():
    provider = ScriptedProvider([FINISH])

    def boundary(_event):
        raise OSError("journal unavailable")

    with pytest.raises(ConsumptionBoundaryError):
        AgentLoop(provider, FakeComputer(), OK,
                  consumption_boundary_sink=boundary).run(GOAL)
    assert provider.prompts == []


def test_search_boundary_precedes_real_search_effect():
    order = []
    computer = SearchComputer()

    class Provider(ScriptedProvider):
        def complete(self, prompt):
            order.append("provider")
            return super().complete(prompt)

    def boundary(event):
        order.append((event.kind, event.ordinal))

    AgentLoop(Provider([SEARCH, FINISH]), computer, OK, enable_search=True,
              consumption_boundary_sink=boundary).run(GOAL)
    assert order[0] == ("model_turn", 1)
    assert order[1] == "provider"
    assert order[2] == ("search_effect", 1)
    assert computer.search_calls == [("boundary-s1-search", "mission")]


def test_search_boundary_sink_failure_prevents_real_search_effect():
    computer = SearchComputer()

    def boundary(event):
        if event.kind == "search_effect":
            raise OSError("journal unavailable")

    with pytest.raises(ConsumptionBoundaryError):
        AgentLoop(ScriptedProvider([SEARCH]), computer, OK, enable_search=True,
                  consumption_boundary_sink=boundary).run(GOAL)
    assert computer.search_calls == []


def test_boundary_sink_none_preserves_default_counts_and_termination():
    provider = ScriptedProvider([FINISH])
    result = AgentLoop(provider, FakeComputer(), OK).run(GOAL)
    assert result.status == "VERIFIED"
    assert result.turns == 1
    assert len(provider.prompts) == 1


def test_multiple_boundaries_are_deterministically_numbered():
    events = []
    AgentLoop(ScriptedProvider([SEARCH, FINISH]), SearchComputer(), OK,
              enable_search=True,
              consumption_boundary_sink=events.append).run(GOAL)
    assert [(event.kind, event.ordinal) for event in events] == [
        ("model_turn", 1), ("search_effect", 1), ("model_turn", 2)
    ]
