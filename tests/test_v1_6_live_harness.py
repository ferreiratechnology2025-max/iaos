import pytest

from application.v16_independent_publisher import extract_v16_fact
from application.execution_trace import MemoryExecutionTrace
from governance.aep_executors import Executor
from scripts.agent_todomvc_e2e import AGNES_KEYS, COMPUTER_KEYS
from scripts.agent_wombat_v16_live import (
    ESA, NASA, PUBLISHER_POLICY, RESEARCH, live_boundary_report,
    build_live_v16_loop, create_live_trace, preflight_live_v16, validate_live_configuration,
)


class FakeProvider:
    provider_id = "agnes"

    def complete(self, prompt):
        raise AssertionError("provider must not be called by the boundary probe")


class FakePort:
    def __init__(self, page_url="https://websitewombat.app/build", aria="Start a new site"):
        self.page_url, self.aria = page_url, aria
        self.calls = []

    def read_control_state(self, request_id):
        return type("Control", (), {"holder": "bot", "requested": False})()

    def navigate(self, *args):
        self.calls.append(("navigate", args[-1]))

    def observe(self):
        return type("Observation", (), {"page_url": self.page_url, "aria_snapshot": self.aria})()


class FakeExecutor(Executor):
    action = opcode = "web.search"

    def run(self, resource, args, identity, binding=None):
        raise AssertionError("executor must not be called by the boundary probe")

    def effects(self):
        return ()


def configs():
    computer = dict.fromkeys(COMPUTER_KEYS, "configured")
    agnes = dict.fromkeys(AGNES_KEYS, "configured")
    brave = {"BRAVE_SEARCH_API_KEY": "configured"}
    return computer, agnes, brave


def test_live_entrypoint_reaches_real_execution_boundary_without_network():
    computer, agnes, brave = configs()
    report = live_boundary_report(
        computer, agnes, brave,
        provider_factory=FakeProvider,
        computer_factory=lambda *args: FakePort(),
        search_factory=FakeExecutor,
    )
    assert report["DEDICATED_LIVE_ENTRYPOINT"] == "YES"
    assert report["AGNES_CALL_WOULD_OCCUR"] == "YES"
    assert report["BRAVE_EXECUTOR_AVAILABLE"] == "YES"
    assert report["COMPUTER_RUNTIME_AVAILABLE"] == "YES"
    assert report["V1_6_STORE_CAPACITY_4_WIRED"] == "YES"
    assert report["V1_6_COMPLETION_POLICY_WIRED"] == "YES"
    assert RESEARCH.max_search_attempts == 4 and RESEARCH.max_deep_read_attempts == 4
    assert PUBLISHER_POLICY.required_publishers == frozenset((NASA.publisher_id, ESA.publisher_id))
    assert extract_v16_fact("Artemis I — Nov. 16, 2022") == NASA.fact


def test_real_live_composition_requires_and_injects_trace_sink():
    computer, agnes, brave = configs()
    trace = MemoryExecutionTrace("live-wiring")
    loop, _session, _provider, _executor, _port = build_live_v16_loop(
        computer, agnes, brave,
        provider_factory=FakeProvider,
        computer_factory=lambda *args: FakePort(),
        search_factory=FakeExecutor,
        trace_sink=trace,
    )
    assert loop._trace_sink is trace
    with pytest.raises(RuntimeError, match="LIVE_TRACE_REQUIRED"):
        build_live_v16_loop(
            computer, agnes, brave,
            provider_factory=FakeProvider,
            computer_factory=lambda *args: FakePort(),
            search_factory=FakeExecutor,
        )


def test_live_trace_creation_is_external_and_fail_closed(tmp_path):
    trace_path = tmp_path / "events.jsonl"
    trace = create_live_trace(trace_path, run_id="test-live")
    trace.emit({"event_type": "TRACE_CREATED", "turn": 0})
    trace.close()
    assert trace_path.exists()
    with pytest.raises(RuntimeError, match="LIVE_TRACE_PATH_PREEXISTING"):
        create_live_trace(trace_path, run_id="test-live-duplicate")


def test_live_trace_inside_repository_is_rejected():
    from scripts import agent_wombat_v16_live
    with pytest.raises(RuntimeError, match="LIVE_TRACE_PATH_INSIDE_REPOSITORY"):
        create_live_trace(agent_wombat_v16_live.__file__, run_id="invalid-live")


def test_missing_agnes_configuration_fails_closed_before_boundary():
    computer, agnes, brave = configs()
    agnes.pop("AGNES_API_KEY")
    with pytest.raises(RuntimeError, match="LIVE_CONFIGURATION_INCOMPLETE"):
        validate_live_configuration(computer, agnes, brave)


def test_missing_brave_or_computer_configuration_fails_closed_before_cognition():
    computer, agnes, brave = configs()
    brave.clear()
    with pytest.raises(RuntimeError, match="LIVE_CONFIGURATION_INCOMPLETE"):
        validate_live_configuration(computer, agnes, brave)
    computer, agnes, brave = configs()
    computer.pop("IAOS_COMPUTER_TOKEN")
    with pytest.raises(RuntimeError, match="LIVE_CONFIGURATION_INCOMPLETE"):
        validate_live_configuration(computer, agnes, brave)


def test_synthetic_entrypoint_remains_non_live():
    from scripts.agent_wombat_v16_e2e import synthetic_wiring_report
    assert synthetic_wiring_report()["live"] is False


def test_preflight_passes_before_loop_boundary_with_doubles(tmp_path):
    computer, agnes, brave = configs()
    ledger = tmp_path / "used.txt"
    report, port = preflight_live_v16(
        computer, agnes, brave, expected_head="head-ok", profile_volume="openbot-profile-wombat-clean-20261009-010101",
        profile_ledger=str(ledger), port_factory=lambda *args: FakePort(),
        mounted_volume_fn=lambda: "openbot-profile-wombat-clean-20261009-010101",
        git_state_fn=lambda: ("head-ok", False),
    )
    assert report["CLEAN_WORLD_GATE"] == "PASS"
    assert report["PROFILE_UNUSED"] == "YES"
    assert report["VALID_SOURCE_COUNT"] == "0"
    assert report["REQUIRED_PUBLISHERS_SATISFIED"] == "0/2"
    assert port is not None


def test_preflight_failure_stops_before_computer_and_cognition(tmp_path):
    computer, agnes, brave = configs()
    called = []
    report, port = preflight_live_v16(
        computer, agnes, brave, expected_head="head-ok", profile_volume="openbot-profile-wombat-clean-20261009-010101",
        profile_ledger=str(tmp_path / "used.txt"), port_factory=lambda *args: called.append(args),
        mounted_volume_fn=lambda: "openbot-profile-wombat-clean-20261009-010101",
        git_state_fn=lambda: ("wrong-head", False),
    )
    assert report["PREFLIGHT_FAILURE"] == "HEAD_MISMATCH"
    assert report["CLEAN_WORLD_GATE"] == "FAIL"
    assert port is None and called == []


def test_default_invocation_requires_explicit_live_flag(capsys):
    from scripts.agent_wombat_v16_live import main
    assert main([]) == 2
    assert "LIVE_EXECUTION_AUTHORIZATION_REQUIRED" in capsys.readouterr().out
