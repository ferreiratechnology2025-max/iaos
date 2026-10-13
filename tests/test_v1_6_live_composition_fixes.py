from types import SimpleNamespace

from application.computer_runtime import OpenBotComputerAdapter
from application.deep_read_transport_trace import TracingTransport
from application.execution_trace import MemoryExecutionTrace
from application.source_facts import DeepReadFact
from governance.aep_executors import Executor
from scripts.agent_todomvc_e2e import COMPUTER_KEYS
from application.v16_independent_publisher import ArtifactObservation
from scripts.agent_wombat_v16_live import (
    ESA, NASA, _live_verifier_factory, build_live_v16_loop, preflight_live_v16,
)


class Provider:
    provider_id = "agnes"

    def complete(self, _prompt):
        raise AssertionError("provider must not be called")


class SearchExecutor(Executor):
    action = opcode = "web.search"

    def run(self, *args, **kwargs):
        raise AssertionError("search must not be called")

    def effects(self):
        return ()


class PreflightAdapter(OpenBotComputerAdapter):
    def read_control_state(self, _request_id=None):
        return SimpleNamespace(holder="bot", requested=False)

    def navigate(self, url):
        return url

    def observe(self):
        return SimpleNamespace(page_url="https://websitewombat.app/build", aria_snapshot="Start a new site")


def _configs():
    return (dict.fromkeys(COMPUTER_KEYS, "configured"),
            {"AGNES_API_KEY": "configured", "AGNES_ENDPOINT": "https://agnes.invalid", "AGNES_MODEL": "model"},
            {"BRAVE_SEARCH_API_KEY": "configured"})


def test_live_verifier_complete_and_incomplete_paths():
    holder = {
        "loop": SimpleNamespace(deep_read_facts=[
            DeepReadFact("search_bound_deep_read", "read-n", "search-n", NASA.canonical_document,
                         "Artemis I Nov. 16, 2022", False, "n" * 64),
            DeepReadFact("search_bound_deep_read", "read-e", "search-e", ESA.canonical_document,
                         "Rosetta 2 March 2004", False, "e" * 64),
        ]),
        "session": SimpleNamespace(search_ledger=[
            {"execution_id": "search-n", "url": NASA.canonical_document},
            {"execution_id": "search-e", "url": ESA.canonical_document},
        ]),
    }
    verifier = _live_verifier_factory(holder)
    observation = ArtifactObservation(
        "space-a", "page-a", 1, "Space Mission Launch Comparison",
        "Artemis I Nov. 16, 2022 Rosetta 2 March 2004",
    )
    assert verifier(observation).verified is True
    holder["loop"].deep_read_facts = []
    incomplete_observation = ArtifactObservation(
        observation.space_id, observation.page_id, observation.revision,
        observation.title, "",
    )
    assert verifier(incomplete_observation).verified is False


def test_actual_preflight_port_path_creates_and_reuses_traced_adapter(tmp_path):
    computer, agnes, brave = _configs()
    trace = MemoryExecutionTrace("composition")
    profile = "openbot-profile-wombat-clean-composition"
    report, port = preflight_live_v16(
        computer, agnes, brave, expected_head="head-ok", profile_volume=profile,
        profile_ledger=str(tmp_path / "used.txt"), port_factory=PreflightAdapter,
        mounted_volume_fn=lambda: profile, git_state_fn=lambda: ("head-ok", False),
        trace_sink=trace,
    )
    assert report["CLEAN_WORLD_GATE"] == "PASS"
    assert isinstance(port._transport, TracingTransport)
    loop, _session, _provider, _executor, reused = build_live_v16_loop(
        computer, agnes, brave, provider_factory=Provider, search_factory=SearchExecutor,
        port=port, trace_sink=trace,
    )
    assert reused is port
    assert loop._trace_sink is trace
    assert isinstance(reused._transport, TracingTransport)
