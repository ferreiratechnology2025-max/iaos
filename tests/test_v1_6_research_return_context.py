"""Synthetic gate for the V1.6 runner-owned research return context.

These tests deliberately avoid live OpenBot, model, search, and OpenDots effects.
"""

from types import SimpleNamespace

import pytest

from application.agent_runtime import GovernedComputerSession, ResearchReturnContext
from application.computer_runtime import ComputerElement, ComputerPageObservation
from application.source_facts import SearchConfig
from governance.bound_read import SearchSourceBinding
from scripts.agent_opendots_v16_live import (
    ESA_CANONICAL_TARGET,
    NASA_CANONICAL_TARGET,
    RESEARCH_RETURN_ANCHOR,
    _establish_research_return_context,
)


class ObservationPort:
    def __init__(self, urls):
        self.urls = list(urls)
        self.navigations = []
        self.observations = 0

    def navigate(self, url):
        self.navigations.append(url)
        return url

    def observe(self):
        url = self.urls[min(self.observations, len(self.urls) - 1)]
        self.observations += 1
        return ComputerPageObservation(
            url, "Page", "session", self.observations,
            (ComputerElement("e1", "button", "Continue"),),
            '- button "Continue" [ref=e1]',
        )


def _session(port, context=None):
    return GovernedComputerSession(
        port,
        allowed_origins=("https://websitewombat.app",),
        search=SearchConfig(("www.nasa.gov",)),
        allow_read=True,
        research_return_context=context,
    )


def _bound(session):
    session.bindings.append(SearchSourceBinding("search-1", 0, NASA_CANONICAL_TARGET, "digest"))


def test_runner_bootstrap_is_pure_in_memory_and_does_not_touch_browser():
    port = ObservationPort(["about:blank"])
    session = _session(port)
    deps = SimpleNamespace(
        openbot_runtime=port,
        research_runtime=(object(), session, object()),
    )

    _establish_research_return_context(deps)

    assert port.navigations == []
    assert port.observations == 0
    assert session._research_return_context == ResearchReturnContext(
        NASA_CANONICAL_TARGET, "runner_bootstrap"
    )
    assert RESEARCH_RETURN_ANCHOR != ESA_CANONICAL_TARGET


def test_runner_bootstrap_survives_navigation_that_would_fail():
    class FailingNavigationPort(ObservationPort):
        def navigate(self, _url):
            raise AssertionError("bootstrap must not navigate")

    port = FailingNavigationPort(["about:blank"])
    session = _session(port)
    deps = SimpleNamespace(
        openbot_runtime=port,
        research_runtime=(object(), session, object()),
    )

    _establish_research_return_context(deps)

    assert session._research_return_context == ResearchReturnContext(
        NASA_CANONICAL_TARGET, "runner_bootstrap"
    )
    assert port.navigations == []
    assert port.observations == 0


def test_model_observations_do_not_overwrite_runner_owned_context():
    port = ObservationPort(["about:blank", "https://websitewombat.app/"])
    context = ResearchReturnContext(NASA_CANONICAL_TARGET, "runner_bootstrap")
    session = _session(port, context)

    session.observe("initial")
    session.observe("after-model-navigation")

    assert session._trusted_page.page_url == "https://websitewombat.app/"
    assert session._research_page_url() == NASA_CANONICAL_TARGET
    assert session._research_allowed_origins() == ("https://www.nasa.gov",)


def test_read_source_uses_explicit_context_even_without_current_observation():
    port = ObservationPort(["about:blank"])
    session = _session(port, ResearchReturnContext(NASA_CANONICAL_TARGET, "runner_bootstrap"))
    _bound(session)
    captured = {}

    def execute(_authorizer, proposal):
        captured["request"] = proposal.args["request"]
        return SimpleNamespace(
            ok=True,
            execution_id="read-1",
            structured_output={
                "final_url": NASA_CANONICAL_TARGET,
                "visible_text": "Artemis I — Nov. 16, 2022",
                "truncated": False,
            },
            result_authority="DATA_ONLY",
            result_provenance="COMPUTER_OBSERVATION",
        )

    session._execute = execute
    outcome = session.read_source("read-step", "search-1")

    assert outcome.ok
    assert '"return_url":"https://www.nasa.gov/mission/artemis-i/"' in captured["request"]
    assert port.navigations == []


@pytest.mark.parametrize(
    "context, expected",
    [
        (None, "no_trusted_page"),
        (ResearchReturnContext("about:blank", "runner_bootstrap"), "return_url_invalid"),
        (ResearchReturnContext("", "runner_bootstrap"), "return_url_invalid"),
    ],
)
def test_missing_or_invalid_research_context_fails_closed_before_source_navigation(context, expected):
    port = ObservationPort(["about:blank"])
    session = _session(port, context)
    _bound(session)
    outcome = session.read_source("read-step", "search-1")

    assert not outcome.ok
    assert outcome.code == expected
    assert port.navigations == []


def test_invalid_bootstrap_target_fails_closed_without_browser_effect(monkeypatch):
    port = ObservationPort(["about:blank"])
    session = _session(port)
    deps = SimpleNamespace(openbot_runtime=port, research_runtime=(object(), session, object()))

    monkeypatch.setattr("scripts.agent_opendots_v16_live.RESEARCH_RETURN_ANCHOR", "about:blank")
    with pytest.raises(RuntimeError, match="research_return_anchor_invalid"):
        _establish_research_return_context(deps)

    assert session._research_return_context is None
    assert port.navigations == []
    assert port.observations == 0
