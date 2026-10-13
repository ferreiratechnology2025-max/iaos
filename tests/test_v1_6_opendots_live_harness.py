import io
import json
import sys
from pathlib import Path
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from application.v16_independent_publisher import ArtifactObservation, make_synthetic_run
from application.agent_loop import AgentLoop, VerificationResult
from application.computer_runtime import ComputerRuntimeError, OpenBotComputerAdapter
from application.execution_trace import MemoryExecutionTrace
from application.source_facts import AdmittedResearchFact, DeepReadFact
from governance.aep_executors import Executor
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse
from scripts.agent_opendots_v16_live import (
    CONTENT,
    ESA_CANONICAL_TARGET,
    OpenDotsHarnessPreflightError,
    LiveDependencies,
    NASA_CANONICAL_TARGET,
    TITLE,
    build_live_dependencies,
    DurableRunJournal,
    live_preflight,
    main,
    prepare_opendots_runtime,
    dispatch_mode,
    run_live_operational,
    run_synthetic_preflight,
)
import scripts.agent_opendots_v16_live as harness


def _ready_navigation_probe():
    return {
        "ready": True,
        "snapshot_status": 200,
        "run_status": 200,
        "snapshot_shape_valid": True,
        "aria_shape_valid": True,
        "page_url_present": True,
        "page_url_canonicalizable": True,
        "navigation_readiness_attempts": 1,
        "navigation_readiness_http_status": 200,
        "navigation_readiness_succeeded": True,
        "post_nav_observation_attempted": True,
        "post_nav_observation_succeeded": True,
        "post_nav_page_url": NASA_CANONICAL_TARGET,
    }


def test_exactly_one_space_is_bound_and_valid_path_completes():
    result = run_synthetic_preflight(spaces=({"id": "space-a"},))
    assert result.completed
    assert result.preflight.space_id == "space-a"
    assert result.counters.page_creates == 1
    assert result.counters.page_reads == 1
    assert result.counters.page_edits == 0
    assert result.counters.wombat_actions == 0


def test_zero_or_multiple_spaces_fail_closed():
    assert not run_synthetic_preflight(spaces=()).completed
    assert not run_synthetic_preflight(spaces=({"id": "a"}, {"id": "b"})).completed


def test_invalid_space_id_fails_closed():
    result = run_synthetic_preflight(spaces=({"id": ""},))
    assert not result.completed
    assert result.failure_reason == "space_id_invalid"


def test_research_gate_precedes_create():
    run = make_synthetic_run()
    incomplete = replace(run, chain_b=replace(run.chain_b, admitted_fact=None))
    result = run_synthetic_preflight(spaces=({"id": "space-a"},), research_run=incomplete)
    assert not result.completed
    assert result.counters.page_creates == 0
    assert not any(event["event"] == "ARTIFACT_CREATE_PROPOSED" for event in result.trace.events)


def test_model_can_only_supply_title_and_content():
    result = run_synthetic_preflight(
        spaces=({"id": "space-a"},),
        model_args={"title": TITLE, "content": CONTENT, "space_id": "other"},
    )
    assert not result.completed
    assert result.failure_reason == "model_args_out_of_contract"


def test_invalid_artifact_after_successful_effect_fails_verifier():
    result = run_synthetic_preflight(
        spaces=({"id": "space-a"},), content="Artemis I — Nov. 16, 2022",
    )
    assert result.counters.page_creates == 1
    assert result.counters.page_reads == 1
    assert not result.completed
    assert result.composition.verification is not None
    assert not result.composition.verification.verified


def test_create_and_read_failures_fail_closed():
    create = run_synthetic_preflight(spaces=({"id": "space-a"},), create_failure=True)
    assert not create.completed
    assert create.counters.page_creates == 0
    assert create.counters.page_reads == 0
    read = run_synthetic_preflight(spaces=({"id": "space-a"},), read_failure=True)
    assert not read.completed
    assert read.counters.page_creates == 1
    assert read.counters.page_reads == 0


def test_receipt_identity_mismatch_prevents_completion():
    tampered = ArtifactObservation("space-a", "page-other", 1, TITLE, CONTENT)
    result = run_synthetic_preflight(spaces=({"id": "space-a"},), tampered_observation=tampered)
    assert not result.completed
    assert result.failure_reason == "receipt_observation_identity_mismatch"


def test_effect_success_does_not_equal_verified_completion():
    result = run_synthetic_preflight(
        spaces=({"id": "space-a"},), content="unverified content",
    )
    assert result.counters.page_creates == 1
    assert not result.completed
    assert any(event["event"] == "STRUCTURED_VERIFIER_FAIL" for event in result.trace.events)


def test_trace_has_no_secret_or_wombat_surface():
    result = run_synthetic_preflight(spaces=({"id": "space-a"},))
    rendered = repr(result.trace.events)
    assert "token" not in rendered.lower()
    assert result.counters.wombat_actions == 0
    assert all("preview" not in event["event"].lower() for event in result.trace.events)


def _live_env():
    return {"OPENDOTS_BASE_URL": "http://127.0.0.1:4310", "OWNER_TOKEN": "runtime-only"}


def _healthy_get(_config, path):
    if path == "/api/state":
        return 200, {"status": "ok"}
    return 200, {"spaces": [{"id": "space-a"}]}


def test_live_preflight_is_read_only_and_binds_one_space():
    calls = []

    def get(config, path):
        calls.append(path)
        return _healthy_get(config, path)

    result = live_preflight(env=_live_env(), readonly_get=get, openbot_health=lambda: True,
                            observation_probe=_ready_navigation_probe)
    assert result.passed
    assert result.bound_space.space_id == "space-a"
    assert calls == ["/api/state", "/api/workspace"]


def test_live_preflight_never_invokes_model_search_or_page_effects():
    counters = {"model": 0, "search": 0, "deep": 0, "page": 0}
    result = live_preflight(env=_live_env(), readonly_get=_healthy_get, openbot_health=lambda: True,
                            observation_probe=_ready_navigation_probe)
    assert result.passed
    assert counters == {"model": 0, "search": 0, "deep": 0, "page": 0}


def test_live_preflight_fails_closed_for_missing_runtime_config():
    result = live_preflight(env={}, readonly_get=_healthy_get, openbot_health=lambda: True)
    assert not result.passed
    assert result.failure_reason == "missing_opendots_base_url"
    assert not result.observation_probe_attempted
    assert not result.observation_ready


def test_live_preflight_fails_closed_for_missing_owner_token():
    result = live_preflight(env={"OPENDOTS_BASE_URL": "http://127.0.0.1:4310"},
                            readonly_get=_healthy_get, openbot_health=lambda: True)
    assert not result.passed
    assert result.failure_reason == "missing_owner_token"
    assert not result.observation_probe_attempted
    assert not result.observation_ready


def test_live_preflight_fails_closed_for_state_or_workspace_errors():
    def bad_state(_config, path):
        return (503, {}) if path == "/api/state" else (200, {"spaces": [{"id": "a"}]})

    assert not live_preflight(env=_live_env(), readonly_get=bad_state,
                              openbot_health=lambda: True).passed

    def bad_workspace(_config, path):
        return (200, {}) if path == "/api/state" else (200, {"spaces": []})

    assert not live_preflight(env=_live_env(), readonly_get=bad_workspace,
                              openbot_health=lambda: True).passed


def test_live_preflight_fails_closed_for_unhealthy_openbot_or_policy():
    assert not live_preflight(env=_live_env(), readonly_get=_healthy_get,
                              openbot_health=lambda: False).passed
    wrong = {"NASA": NASA_CANONICAL_TARGET, "ESA": "https://example.invalid"}
    result = live_preflight(env=_live_env(), readonly_get=_healthy_get,
                            openbot_health=lambda: True, target_policy=wrong)
    assert not result.passed
    assert not result.target_policy_ready


def test_live_preflight_requires_exactly_one_space():
    def two_spaces(_config, path):
        return (200, {"spaces": [{"id": "a"}, {"id": "b"}]}) if path == "/api/workspace" else (200, {})

    result = live_preflight(env=_live_env(), readonly_get=two_spaces,
                            openbot_health=lambda: True)
    assert not result.passed
    assert result.failure_reason == "space_count_must_equal_one"


def test_live_preflight_uses_frozen_targets_without_fetching_them():
    requested = []

    def get(config, path):
        requested.append(path)
        return _healthy_get(config, path)

    result = live_preflight(env=_live_env(), readonly_get=get, openbot_health=lambda: True,
                            observation_probe=_ready_navigation_probe,
                            target_policy={"NASA": NASA_CANONICAL_TARGET, "ESA": ESA_CANONICAL_TARGET})
    assert result.passed
    assert requested == ["/api/state", "/api/workspace"]


def test_openbot_health_accepts_only_http_200_status_ok(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"status":"ok","profile":{"running":false}}'

    calls = []
    monkeypatch.setattr(harness, "urlopen", lambda request, timeout: (calls.append(request), Response())[1])
    computer = {
        "IAOS_COMPUTER_URL": "http://127.0.0.1:55248",
        "IAOS_COMPUTER_BOT_ID": "bot",
        "IAOS_COMPUTER_TOKEN": "token",
    }
    assert harness._openbot_health_check(computer)
    assert calls[0].full_url == "http://127.0.0.1:55248/health"


def test_opendots_startup_contract_uses_compiled_entrypoint_and_tcp_readiness(tmp_path, monkeypatch):
    entrypoint = tmp_path / "dist" / "server" / "server" / "index.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("", encoding="utf-8")

    class FakeProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    calls = []
    fake = FakeProcess()

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return fake

    monkeypatch.setattr(harness.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(harness, "wait_for_opendots_listener", lambda **kwargs: None)
    runtime = harness.start_opendots_runtime(
        root=tmp_path,
        env={"OWNER_TOKEN": "runtime-only"},
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
    )

    assert calls[0][0] == list(harness.OPENDOTS_STARTUP_COMMAND)
    assert calls[0][1]["cwd"] == Path(tmp_path)
    assert calls[0][1]["env"]["HOST"] == "127.0.0.1"
    assert calls[0][1]["env"]["PORT"] == "4310"
    assert runtime.stdout_path.exists()
    assert runtime.stderr_path.exists()


def test_opendots_startup_fails_closed_without_compiled_entrypoint(tmp_path):
    with pytest.raises(harness.OpenDotsHarnessPreflightError,
                       match="compiled_opendots_entrypoint_missing"):
        harness.start_opendots_runtime(
            root=tmp_path,
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
        )


def test_opendots_external_pair_is_reused_without_start_or_stop():
    starter_calls = []
    lease, runtime_env = prepare_opendots_runtime(
        env={"OPENDOTS_BASE_URL": "http://external.invalid:4310", "OWNER_TOKEN": "external-token"},
        starter=lambda **kwargs: starter_calls.append(kwargs),
        port_occupied=lambda: True,
    )
    try:
        assert not lease.owned
        assert lease.config.base_url == "http://external.invalid:4310"
        assert runtime_env["OWNER_TOKEN"] == "external-token"
        assert starter_calls == []
    finally:
        lease.close()
    assert starter_calls == []


@pytest.mark.parametrize("env", [
    {"OPENDOTS_BASE_URL": "http://127.0.0.1:4310"},
    {"OWNER_TOKEN": "partial-token"},
])
def test_opendots_partial_pair_fails_closed_without_start(env):
    with pytest.raises(OpenDotsHarnessPreflightError, match="opendots_runtime_config_partial"):
        prepare_opendots_runtime(env=env, starter=lambda **kwargs: pytest.fail("must not start"),
                                 port_occupied=lambda: False)


def test_opendots_managed_runtime_shares_ephemeral_token_and_cleans_up(tmp_path):
    captured = {}
    stops = []

    class FakeProcess:
        def stop(self):
            stops.append(True)

    def starter(**kwargs):
        captured.update(kwargs)
        return FakeProcess()

    lease, runtime_env = prepare_opendots_runtime(
        env={}, root=tmp_path, starter=starter, port_occupied=lambda: False,
    )
    runtime_dir = lease.cleanup_dir
    token = runtime_env["OWNER_TOKEN"]
    assert lease.owned
    assert lease.config.base_url == "http://127.0.0.1:4310"
    assert token == captured["env"]["OWNER_TOKEN"]
    assert len(token) >= 43
    assert captured["env"]["DATABASE_PATH"].startswith(str(runtime_dir))
    lease.close()
    assert stops == [True]
    assert not runtime_dir.exists()


def test_opendots_managed_port_occupied_fails_closed():
    with pytest.raises(OpenDotsHarnessPreflightError, match="managed_opendots_port_occupied"):
        prepare_opendots_runtime(env={}, starter=lambda **kwargs: pytest.fail("must not start"),
                                 port_occupied=lambda: True)


def test_opendots_managed_start_failure_cleans_ephemeral_runtime_dir(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "managed-runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(harness.tempfile, "mkdtemp", lambda **kwargs: str(runtime_dir))

    def failing_starter(**kwargs):
        raise RuntimeError("startup-failed")

    with pytest.raises(RuntimeError, match="startup-failed"):
        prepare_opendots_runtime(env={}, starter=failing_starter, port_occupied=lambda: False)
    assert not runtime_dir.exists()


def test_opendots_start_failure_preserves_sanitized_evidence_before_cleanup(tmp_path, monkeypatch):
    entrypoint = tmp_path / "dist" / "server" / "server" / "index.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("", encoding="utf-8")
    runtime_dir = tmp_path / "managed-runtime"
    monkeypatch.setattr(harness.tempfile, "mkdtemp", lambda **kwargs: str(runtime_dir))

    class FailedProcess:
        returncode = 134

        def poll(self):
            return self.returncode

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            return None

    def fake_popen(_args, **kwargs):
        synthetic_secret = kwargs["env"]["OWNER_TOKEN"]
        kwargs["stdout"].write(f"fatal {synthetic_secret}\n")
        kwargs["stdout"].flush()
        kwargs["stderr"].write(f"Error: OWNER_TOKEN={synthetic_secret}\n at server.js:12\n")
        kwargs["stderr"].flush()
        database = Path(kwargs["env"]["DATABASE_PATH"])
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"sqlite")
        return FailedProcess()

    monkeypatch.setattr(harness.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        harness, "wait_for_opendots_listener",
        lambda **kwargs: (_ for _ in ()).throw(
            harness.OpenDotsHarnessPreflightError("opendots_process_exited:134")
        ),
    )

    with pytest.raises(harness.OpenDotsHarnessPreflightError) as raised:
        prepare_opendots_runtime(env={}, root=tmp_path, port_occupied=lambda: False)

    evidence = raised.value.startup_failure_evidence
    assert evidence is not None
    assert evidence.process_exit_code == 134
    assert evidence.stdout_present is True
    assert evidence.stderr_present is True
    safe_logs = "\n".join(evidence.stdout_last_safe_lines + evidence.stderr_last_safe_lines)
    assert "synthetic-secret" not in safe_logs
    assert "<REDACTED>" in safe_logs
    assert evidence.first_fatal_error_class == "ERROR"
    assert evidence.stacktrace_present is True
    assert evidence.database_file_existed_before_cleanup is True
    assert evidence.database_size_bytes_before_cleanup == 6
    assert evidence.failure_stage == "startup_process_exit"
    assert not runtime_dir.exists()


def test_opendots_startup_success_has_no_failure_evidence(tmp_path, monkeypatch):
    entrypoint = tmp_path / "dist" / "server" / "server" / "index.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("", encoding="utf-8")

    class RunningProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = RunningProcess()
    monkeypatch.setattr(harness.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(harness, "wait_for_opendots_listener", lambda **kwargs: None)
    runtime = harness.start_opendots_runtime(
        root=tmp_path, env={}, stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
    )
    assert runtime.process is process
    assert not hasattr(runtime, "startup_failure_evidence")


def test_opendots_managed_exception_cleanup_and_token_not_in_transport_artifacts(tmp_path):
    captured = {}
    stops = []

    class FakeProcess:
        def stop(self):
            stops.append(True)

    def starter(**kwargs):
        captured.update(kwargs)
        return FakeProcess()

    lease, runtime_env = prepare_opendots_runtime(
        env={}, root=tmp_path, starter=starter, port_occupied=lambda: False,
    )
    token = runtime_env["OWNER_TOKEN"]
    try:
        raise RuntimeError("synthetic-post-start-failure")
    except RuntimeError:
        lease.close()

    assert stops == [True]
    assert token not in " ".join(captured["args"]) if "args" in captured else True
    assert token not in repr(captured.get("stdout_path"))
    assert token not in repr(captured.get("stderr_path"))
    assert not lease.cleanup_dir.exists()


def test_observation_failure_preserves_sanitized_exception_details():
    class FailingComputer:
        def observe(self, _step_id):
            raise RuntimeError("sentinel_observation_failure")

    class Provider:
        provider_id = "test"

        def complete(self, _prompt):
            return LLMResponse('{"kind":"finish","claim":"done"}', "m", "test")

    result = AgentLoop(
        Provider(), FailingComputer(),
        lambda _observation: VerificationResult(False, {}),
    ).run(AgentGoal("observation", "observe"))
    assert result.status == "OBSERVATION_FAILED"
    assert result.observation_exception_type == "RuntimeError"
    assert result.observation_exception_message == "sentinel_observation_failure"
    assert result.observation_failure_stage == "OBSERVATION"


def test_preflight_separates_service_health_from_observation_readiness():
    failed = live_preflight(
        env=_live_env(), readonly_get=_healthy_get, openbot_health=lambda: True,
        observation_probe=lambda: {
            "ready": False, "exception_type": "RuntimeError",
            "exception_message": "computer_snapshot_shape",
        },
    )
    assert not failed.passed
    assert not failed.observation_ready
    assert failed.failure_reason == "observation_not_ready"
    assert failed.observation_exception_message == "computer_snapshot_shape"

    passed = live_preflight(
        env=_live_env(), readonly_get=_healthy_get, openbot_health=lambda: True,
        observation_probe=lambda: {
            "ready": True, "snapshot_status": 200, "run_status": 200,
            "snapshot_shape_valid": True, "aria_shape_valid": True,
            "page_url_present": True, "page_url_canonicalizable": True,
            "navigation_readiness_attempts": 1,
            "navigation_readiness_http_status": 200,
            "navigation_readiness_succeeded": True,
            "post_nav_observation_attempted": True,
            "post_nav_observation_succeeded": True,
            "post_nav_page_url": NASA_CANONICAL_TARGET,
        },
    )
    assert passed.passed and passed.observation_ready
    assert passed.observation_probe_attempted


def test_preflight_successful_probe_and_failed_probe_are_reported_fail_closed():
    successful = live_preflight(
        env=_live_env(), readonly_get=_healthy_get, openbot_health=lambda: True,
        observation_probe=_ready_navigation_probe,
    )
    assert successful.observation_probe_attempted
    assert successful.observation_ready


def test_preflight_navigation_readiness_fails_closed_without_duplicate_calls():
    calls = []
    failed = live_preflight(
        env=_live_env(), readonly_get=_healthy_get, openbot_health=lambda: True,
        observation_probe=lambda: calls.append("probe") or {
            "ready": True,
            "navigation_readiness_attempts": 1,
            "navigation_readiness_http_status": 502,
            "navigation_readiness_succeeded": False,
            "post_nav_observation_attempted": False,
            "post_nav_observation_succeeded": False,
        },
    )
    assert calls == ["probe"]
    assert not failed.passed
    assert failed.failure_reason == "navigation_readiness_failed"
    assert failed.navigation_readiness_attempts == 1
    assert not failed.post_nav_observation_attempted


def test_preflight_navigation_success_requires_post_navigation_observation():
    failed = live_preflight(
        env=_live_env(), readonly_get=_healthy_get, openbot_health=lambda: True,
        observation_probe=lambda: {
            "ready": True,
            "navigation_readiness_attempts": 1,
            "navigation_readiness_http_status": 200,
            "navigation_readiness_succeeded": True,
            "post_nav_observation_attempted": True,
            "post_nav_observation_succeeded": False,
        },
    )
    assert not failed.passed
    assert failed.failure_reason == "post_navigation_observation_not_ready"


def test_real_preflight_probe_reuses_one_adapter_for_observe_navigate_observe(monkeypatch):
    calls = []

    class Adapter:
        def __init__(self, *args, **kwargs):
            calls.append("construct")

        def observe(self):
            calls.append("observe")
            return SimpleNamespace(page_url=NASA_CANONICAL_TARGET, aria_snapshot="aria")

        def navigate(self, url):
            calls.append(("navigate", url))
            return url

    monkeypatch.setattr(harness, "OpenBotComputerAdapter", Adapter)
    result = harness._openbot_observation_probe({
        "IAOS_COMPUTER_URL": "http://computer",
        "IAOS_COMPUTER_BOT_ID": "bot",
        "IAOS_COMPUTER_TOKEN": "token",
    })
    assert calls == ["construct", "observe", ("navigate", NASA_CANONICAL_TARGET), "observe"]
    assert result["navigation_readiness_attempts"] == 1
    assert result["navigation_readiness_succeeded"] is True
    assert result["post_nav_observation_succeeded"] is True
    assert result["post_nav_page_url"] == NASA_CANONICAL_TARGET

    failed = live_preflight(
        env=_live_env(), readonly_get=_healthy_get, openbot_health=lambda: True,
        observation_probe=lambda: {
            "ready": False,
            "exception_type": "RuntimeError",
            "exception_message": "computer_snapshot_shape",
        },
    )
    assert failed.observation_probe_attempted
    assert not failed.observation_ready
    assert failed.observation_exception_type == "RuntimeError"


def test_cli_early_return_cannot_report_observation_success(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["agent_opendots_v16_live.py", "--mode", "live-preflight"])
    monkeypatch.delenv("OPENDOTS_BASE_URL", raising=False)
    monkeypatch.delenv("OWNER_TOKEN", raising=False)
    monkeypatch.setattr(
        harness,
        "prepare_opendots_runtime",
        lambda: (SimpleNamespace(close=lambda: None), {}),
    )
    monkeypatch.setattr(
        harness,
        "_configured_values",
        lambda keys, file_env, values_source: {},
    )
    assert harness.main() == 1
    output = capsys.readouterr().out
    assert "OBSERVATION_PROBE_ATTEMPTED=NO" in output
    assert "COMPUTER_PAGE_OBSERVATION_CREATED=NO" in output
    assert "OBSERVATION_READY=NO" in output


def test_openbot_health_fails_closed_for_bad_status_or_response(monkeypatch):
    class Response:
        def __init__(self, status, body):
            self.status = status
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.body

    computer = {
        "IAOS_COMPUTER_URL": "http://computer.invalid",
        "IAOS_COMPUTER_BOT_ID": "bot",
        "IAOS_COMPUTER_TOKEN": "token",
    }
    monkeypatch.setattr(harness, "urlopen", lambda request, timeout: Response(503, b'{"status":"ok"}'))
    assert not harness._openbot_health_check(computer)
    monkeypatch.setattr(harness, "urlopen", lambda request, timeout: Response(200, b'{"status":"busy"}'))
    assert not harness._openbot_health_check(computer)
    monkeypatch.setattr(harness, "urlopen", lambda request, timeout: (_ for _ in ()).throw(TimeoutError()))
    assert not harness._openbot_health_check(computer)
    assert not harness._openbot_health_check({})


def test_cli_live_preflight_injects_computer_health(monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("OPENDOTS_BASE_URL", "http://external.invalid:4310")
    monkeypatch.setenv("OWNER_TOKEN", "external-token")
    monkeypatch.setenv("COMPUTER_ENV_FILE", "computer-runtime.env")
    monkeypatch.setattr(
        harness,
        "_configured_values",
        lambda keys, file_env, values_source: {
            "IAOS_COMPUTER_URL": "http://computer.invalid",
            "IAOS_COMPUTER_BOT_ID": "bot",
            "IAOS_COMPUTER_TOKEN": "token",
        },
    )
    monkeypatch.setattr(harness, "_openbot_health_check", lambda computer: calls.append(computer) or True)
    monkeypatch.setattr(
        harness,
        "live_preflight",
            lambda openbot_health=None, observation_probe=None, **_kwargs: SimpleNamespace(
            passed=bool(openbot_health and openbot_health()),
            state_status=200,
            workspace_status=200,
            bound_space=SimpleNamespace(space_id="space-a"),
            openbot_healthy=True,
            target_policy_ready=True,
        ),
    )
    monkeypatch.setattr("sys.argv", ["agent_opendots_v16_live.py", "--mode", "live-preflight"])
    assert harness.main() == 0
    assert len(calls) == 1
    assert calls[0]["IAOS_COMPUTER_URL"] == "http://computer.invalid"
    assert "OPENBOT_HEALTHY=YES" in capsys.readouterr().out


def test_real_cli_live_run_dispatches_operational_runner(monkeypatch, capsys, tmp_path):
    called = []
    dependencies = object()

    def fake_factory(**_kwargs):
        return dependencies

    def fake_runner(received):
        called.append(received)
        return {"completed": True, "page_creates": 1, "page_reads": 1}

    monkeypatch.setattr("scripts.agent_opendots_v16_live.run_live_operational", fake_runner)
    monkeypatch.setattr("scripts.agent_opendots_v16_live.build_live_dependencies", fake_factory)
    assert main.__module__ == "scripts.agent_opendots_v16_live"
    monkeypatch.setattr("sys.argv", ["agent_opendots_v16_live.py", "--mode", "live-run",
                                      "--run-journal-path", str(tmp_path / "run.jsonl")])
    assert main() == 0
    assert called == [dependencies]
    assert "LIVE_RUN_NOT_AUTHORIZED" not in capsys.readouterr().out


def test_live_run_dispatch_reaches_governed_composition_with_fake_effects():
    calls = []

    def fake_runner(_dependencies):
        result = run_synthetic_preflight(spaces=({"id": "space-a"},))
        calls.extend([result.counters.page_creates, result.counters.page_reads])
        return result

    result = dispatch_mode("live-run", live_runner=fake_runner,
                           dependency_factory=lambda: object())
    assert result.completed
    assert calls == [1, 1]


def test_preflight_modes_never_dispatch_live_runner():
    called = []

    def forbidden_runner():
        called.append(True)
        raise AssertionError("live runner must not be reached")

    assert dispatch_mode("synthetic-preflight", live_runner=forbidden_runner)["live"] is False
    result = live_preflight(env={}, readonly_get=_healthy_get, openbot_health=lambda: True)
    assert not result.passed
    assert called == []


class _BoundaryProvider:
    provider_id = "agnes"

    def complete(self, _prompt):
        raise AssertionError("model boundary must not be crossed")


class _BoundarySearch(Executor):
    action = opcode = "web.search"

    def run(self, *args, **kwargs):
        raise AssertionError("search boundary must not be crossed")

    def effects(self):
        return ()


class _BoundaryComputer(OpenBotComputerAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


def _live_dependency_inputs():
    return {
        "OPENDOTS_BASE_URL": "http://opendots.invalid",
        "OWNER_TOKEN": "owner-runtime-only",
    }, {
        "IAOS_COMPUTER_URL": "http://computer.invalid",
        "IAOS_COMPUTER_BOT_ID": "bot",
        "IAOS_COMPUTER_TOKEN": "computer-runtime-only",
    }, {
        "AGNES_API_KEY": "agnes-runtime-only",
        "AGNES_ENDPOINT": "https://agnes.invalid",
        "AGNES_MODEL": "model",
    }, {"BRAVE_SEARCH_API_KEY": "brave-runtime-only"}


def test_live_dependency_factory_wires_real_components_without_effects():
    env, computer, agnes, brave = _live_dependency_inputs()
    deps = build_live_dependencies(
        env={**env, **computer, **agnes, **brave}, computer=computer, agnes=agnes, brave=brave,
        provider_factory=_BoundaryProvider, computer_factory=_BoundaryComputer,
        search_factory=_BoundarySearch,
    )
    assert deps.model_runtime.provider_id == "agnes"
    assert deps.research_runtime[0]._research_completion_policy is not None
    assert isinstance(deps.openbot_runtime, _BoundaryComputer)
    assert deps.opendots_config.base_url == env["OPENDOTS_BASE_URL"]
    assert deps.governed_create_executor.__class__.__name__ == "OpenDotsPageCreateExecutor"
    assert deps.governed_read_executor.__class__.__name__ == "OpenDotsPageReadExecutor"
    assert deps.research_policy is not None
    assert deps.structured_verifier is not None


def test_live_dependency_factory_wires_restore_sink_into_existing_adapter_path(monkeypatch):
    env, computer, agnes, brave = _live_dependency_inputs()
    captured = {}

    def fake_loop_builder(*_args, **kwargs):
        captured["port"] = kwargs["port"]
        return object(), object(), object(), object(), kwargs["port"]

    monkeypatch.setattr(harness, "build_live_v16_loop", fake_loop_builder)
    deps = build_live_dependencies(
        env={**env, **computer, **agnes, **brave}, computer=computer, agnes=agnes, brave=brave,
        provider_factory=lambda: object(), search_factory=lambda: object(),
    )
    assert captured["port"] is deps.openbot_runtime
    assert callable(captured["port"]._restore_diagnostic_sink)

    captured["port"]._restore_diagnostic_sink({
        "requested_return_url": "https://websitewombat.app/build",
        "navigation_attempted": True, "navigation_succeeded": True,
        "navigation_final_url": "https://websitewombat.app/build",
        "observation_attempted": True, "observation_succeeded": True,
        "observed_url": "https://websitewombat.app/build",
        "nav_url_matches_return": True, "observed_url_matches_return": True,
        "failure_substage": None,
        "content": "must not be copied",
        "token": "must not be copied",
    })
    captured["port"]._restore_diagnostic_sink({
        "requested_return_url": "https://websitewombat.app/build",
        "navigation_attempted": True, "navigation_succeeded": False,
        "navigation_final_url": None, "observation_attempted": False,
        "observation_succeeded": False, "observed_url": None,
        "nav_url_matches_return": None, "observed_url_matches_return": None,
        "failure_substage": "navigation",
    })
    assert deps.restore_diagnostics[0]["restore_diagnostic_index"] == 1
    assert deps.restore_diagnostics[1]["restore_diagnostic_index"] == 2
    assert "content" not in deps.restore_diagnostics[0]
    assert "token" not in deps.restore_diagnostics[0]
    assert [event["event_type"] for event in deps.trace_sink.events
            if event.get("event_type") == "RESTORE_DIAGNOSTIC"] == [
                "RESTORE_DIAGNOSTIC", "RESTORE_DIAGNOSTIC"
            ]

    report = harness._live_run_report(deps, {"completed": False})
    assert report.restore_diagnostics_count == 2
    assert report.restore_failure_diagnostics_count == 1
    serialized = harness._serialize_live_result(report)
    assert "RESTORE_DIAGNOSTIC" not in serialized
    assert "websitewombat.app/build" in serialized
    assert "must not be copied" not in serialized


def test_live_run_report_preserves_restore_order_and_safe_serialization():
    dependencies = SimpleNamespace(restore_diagnostics=[
        {"restore_diagnostic_index": 1, "failure_substage": "navigation",
         "requested_return_url": "https://websitewombat.app/build"},
        {"restore_diagnostic_index": 2, "failure_substage": "observation",
         "requested_return_url": "https://websitewombat.app/build"},
        {"restore_diagnostic_index": 3, "failure_substage": None,
         "requested_return_url": "https://websitewombat.app/build"},
    ])
    report = harness._live_run_report(dependencies, {"completed": False})
    assert [item["restore_diagnostic_index"] for item in report.restore_diagnostics] == [1, 2, 3]
    assert report.restore_diagnostics_count == 3
    assert report.restore_failure_diagnostics_count == 2
    assert harness._serialize_live_result(report).encode("cp1252")


def test_cli_live_run_builds_dependencies_once_and_reaches_pre_effect_boundary():
    built = []
    reached = []
    deps = object()

    def factory():
        built.append(True)
        return deps

    def runner(received):
        assert received is deps
        reached.append(True)
        return {"LIVE_EXECUTION_READY": "YES", "FIRST_EXTERNAL_EFFECT_REACHED": "NO"}

    result = dispatch_mode("live-run", dependency_factory=factory, live_runner=runner)
    assert result["LIVE_EXECUTION_READY"] == "YES"
    assert built == [True]
    assert reached == [True]


def test_cli_live_run_calls_existing_agent_loop_once_and_propagates_result(monkeypatch, capsys, tmp_path):
    calls = []
    goal = object()
    result = {"status": "OBSERVATION_FAILED", "turns": 1}

    class Loop:
        def run(self, received_goal):
            calls.append(received_goal)
            return result

    dependencies = SimpleNamespace(agent_loop=Loop(), high_level_goal=goal)
    monkeypatch.setattr("scripts.agent_opendots_v16_live.build_live_dependencies",
                        lambda **_kwargs: dependencies)
    monkeypatch.setattr("sys.argv", ["agent_opendots_v16_live.py", "--mode", "live-run",
                                      "--run-journal-path", str(tmp_path / "run.jsonl")])
    assert main() == 0
    assert calls == [goal]
    assert capsys.readouterr().out.strip() == (
        'LIVE_RUN_RESULT={"status": "OBSERVATION_FAILED", "turns": 1}'
    )


def test_run_live_operational_propagates_loop_exception_and_result():
    result = object()

    class Loop:
        def run(self, _goal):
            return result

    dependencies = SimpleNamespace(agent_loop=Loop(), high_level_goal=object())
    assert run_live_operational(dependencies) is result

    class FailingLoop:
        def run(self, _goal):
            raise RuntimeError("synthetic_loop_failure")

    failing = SimpleNamespace(agent_loop=FailingLoop(), high_level_goal=object())
    try:
        run_live_operational(failing)
    except RuntimeError as exc:
        assert str(exc) == "synthetic_loop_failure"
    else:
        raise AssertionError("loop exception must propagate fail-closed")


def test_direct_runner_without_dependencies_remains_fail_closed():
    from scripts.agent_opendots_v16_live import run_live_operational

    try:
        run_live_operational()
    except RuntimeError as exc:
        assert str(exc) == "live_runner_dependencies_required"
    else:
        raise AssertionError("direct runner must fail closed")


def test_real_live_dependencies_probe_observation_before_loop():
    from scripts.agent_opendots_v16_live import LiveDependencies, run_live_operational

    calls = []

    class Adapter:
        def navigate(self, _url):
            calls.append("navigate")
            return NASA_CANONICAL_TARGET

        def observe(self):
            calls.append("observe")
            return SimpleNamespace(page_url=NASA_CANONICAL_TARGET)

    class Loop:
        def run(self, _goal):
            calls.append("loop")
            return SimpleNamespace(research_handoff=False, status="OBSERVATION_FAILED")

    deps = LiveDependencies(
        model_runtime=object(),
        research_runtime=(object(), SimpleNamespace(set_research_return_context=lambda _context: calls.append("context")), object()),
        openbot_runtime=Adapter(),
        opendots_config=object(), governed_create_executor=object(),
        governed_read_executor=object(), research_policy=object(),
        structured_verifier=object(), trace_sink=MemoryExecutionTrace("jit"),
        agent_loop=Loop(), high_level_goal=object(),
    )
    run_live_operational(deps)
    assert calls == ["context", "observe", "loop"]


def test_jit_observation_failure_aborts_before_loop_and_preserves_exception():
    from scripts.agent_opendots_v16_live import LiveDependencies, run_live_operational

    calls = []

    class Adapter:
        def navigate(self, _url):
            calls.append("navigate")
            return NASA_CANONICAL_TARGET

        def observe(self):
            calls.append("observe")
            raise ComputerRuntimeError("computer_snapshot_rejected:504")

    class Loop:
        def run(self, _goal):
            calls.append("loop")
            raise AssertionError("loop must not run")

    deps = LiveDependencies(
        model_runtime=object(),
        research_runtime=(object(), SimpleNamespace(set_research_return_context=lambda _context: calls.append("context")), object()),
        openbot_runtime=Adapter(),
        opendots_config=object(), governed_create_executor=object(),
        governed_read_executor=object(), research_policy=object(),
        structured_verifier=object(), trace_sink=MemoryExecutionTrace("jit-fail"),
        agent_loop=Loop(), high_level_goal=object(),
    )
    try:
        run_live_operational(deps)
    except ComputerRuntimeError as exc:
        assert str(exc) == "computer_snapshot_rejected:504"
    else:
        raise AssertionError("JIT failure must abort before loop")
    assert calls == ["context", "observe"]


def test_cli_jit_failure_is_pre_run_operational_failure(monkeypatch, capsys, tmp_path):
    calls = []

    class Adapter:
        def navigate(self, _url):
            calls.append("navigate")
            return NASA_CANONICAL_TARGET

        def observe(self):
            calls.append("observe")
            raise ComputerRuntimeError("computer_snapshot_rejected:504")

    class Loop:
        def run(self, _goal):
            calls.append("loop")
            raise AssertionError("loop must not run")

    deps = LiveDependencies(
        model_runtime=object(),
        research_runtime=(object(), SimpleNamespace(set_research_return_context=lambda _context: calls.append("context")), object()),
        openbot_runtime=Adapter(),
        opendots_config=object(), governed_create_executor=object(),
        governed_read_executor=object(), research_policy=object(),
        structured_verifier=object(), trace_sink=MemoryExecutionTrace("jit-cli"),
        agent_loop=Loop(), high_level_goal=object(),
    )
    monkeypatch.setattr(harness, "build_live_dependencies", lambda **_kwargs: deps)
    monkeypatch.setattr(sys, "argv", ["agent_opendots_v16_live.py", "--mode", "live-run",
                                        "--run-journal-path", str(tmp_path / "run.jsonl")])
    assert main() == 1
    assert calls == ["context", "observe"]
    assert capsys.readouterr().out.strip() == (
        "LIVE_RUN_OPERATIONAL_FAILURE=computer_snapshot_rejected:504"
    )


def test_cli_branch_has_no_unconditional_live_run_refusal():
    import inspect
    from scripts import agent_opendots_v16_live as harness

    source = inspect.getsource(harness.main)
    assert "LIVE_RUN_NOT_AUTHORIZED" not in source


def test_opendots_live_loop_requests_research_handoff_without_changing_legacy_default(monkeypatch):
    captured = []

    def fake_builder(*args, **kwargs):
        captured.append(kwargs["stop_after_research_gate"])
        return object(), object()

    monkeypatch.setattr("scripts.agent_wombat_v16_live.build_agent_loop", fake_builder)
    from scripts.agent_wombat_v16_live import build_live_v16_loop

    computer = dict.fromkeys(("IAOS_COMPUTER_URL", "IAOS_COMPUTER_BOT_ID", "IAOS_COMPUTER_TOKEN"), "x")
    agnes = {"AGNES_API_KEY": "x", "AGNES_ENDPOINT": "https://a.invalid", "AGNES_MODEL": "m"}
    brave = {"BRAVE_SEARCH_API_KEY": "x"}
    build_live_v16_loop(computer, agnes, brave,
                        provider_factory=lambda: object(), computer_factory=lambda *a, **k: object(),
                        search_factory=lambda: object(), trace_sink=MemoryExecutionTrace("handoff"))
    assert captured == [False]


class _HandoffExecutor:
    def __init__(self, action):
        self.arg_names = (("content", "space_id", "title") if action.endswith("create")
                          else ("expected_revision", "page_id", "space_id"))
        self.calls = []

    def run(self, resource, args, identity):
        self.calls.append((resource, args, identity))
        values = dict(args)
        if len(self.arg_names) == 3 and "title" in values:
            return SimpleNamespace(content={"space_id": values["space_id"], "page_id": "p1",
                                            "revision": 1, "title": values["title"],
                                            "content": values["content"]})
        return SimpleNamespace(content={"space_id": values["space_id"], "page_id": values["page_id"],
                                        "revision": int(values["expected_revision"]),
                                        "title": TITLE, "content": CONTENT})


def test_live_runner_handoff_uses_opendots_create_then_receipt_read(monkeypatch):
    from scripts.agent_opendots_v16_live import run_live_operational

    class Loop:
        def run(self, _goal):
            facts = [
                    DeepReadFact("search_bound_deep_read", "rn", "sn", NASA_CANONICAL_TARGET,
                                 "Artemis I Nov. 16, 2022", False, "n"),
                    DeepReadFact("search_bound_deep_read", "re", "se", ESA_CANONICAL_TARGET,
                                 "Rosetta 2 March 2004", False, "e"),
                ]
            return _adapter_result(facts)

    create = _HandoffExecutor("opendots.page.create")
    read = _HandoffExecutor("opendots.page.read")
    deps = SimpleNamespace(agent_loop=Loop(), high_level_goal=object(), space_id="space-a",
                           governed_create_executor=create, governed_read_executor=read,
                           trace_sink=MemoryExecutionTrace("handoff"))
    result = run_live_operational(deps)
    assert result.completed
    assert len(create.calls) == 1
    assert len(read.calls) == 1
    assert dict(read.calls[0][1]) == {"expected_revision": "1", "page_id": "p1", "space_id": "space-a"}


def test_live_runner_handoff_enters_existing_composition_once(monkeypatch):
    calls = []

    class Composition:
        def __init__(self, research_run, space_id, _create, _read):
            calls.append((research_run, space_id))
            self.research_complete = True

        def propose_and_verify(self, *, title, content):
            return {"completed": True, "title": title, "content": content}

    class Loop:
        def run(self, _goal):
            facts = _adapter_facts()
            result = _adapter_result(facts)
            result.status = "RESEARCH_HANDOFF"
            result.research_handoff = True
            return result

    monkeypatch.setattr(harness, "V16OpenDotsArtifactComposition", Composition)
    deps = SimpleNamespace(agent_loop=Loop(), high_level_goal=object(), space_id="space-a",
                           governed_create_executor=object(), governed_read_executor=object())
    result = run_live_operational(deps)
    assert result["completed"] is True
    assert len(calls) == 1
    assert calls[0][1] == "space-a"
    assert calls[0][0].chain_a.publisher.publisher_id == "NASA"
    assert calls[0][0].chain_b.publisher.publisher_id == "ESA"


def test_live_runner_handoff_without_research_state_fails_closed_without_composition(monkeypatch):
    monkeypatch.setattr(
        harness, "V16OpenDotsArtifactComposition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("composition called")),
    )

    class Loop:
        def run(self, _goal):
            return SimpleNamespace(status="RESEARCH_HANDOFF", research_handoff=True,
                                   deep_read_facts=[])

    result = run_live_operational(SimpleNamespace(agent_loop=Loop(), high_level_goal=object()))
    assert result.status == "RESEARCH_HANDOFF"


def test_real_cli_handoff_to_opendots_composition_and_serialization(monkeypatch, capsys, tmp_path):
    calls = []

    class Composition:
        def __init__(self, research_run, _space_id, _create, _read):
            calls.append(research_run)
            self.research_complete = True

        def propose_and_verify(self, *, title, content):
            return {"completed": True, "title": title, "content": content}

    class Loop:
        def run(self, _goal):
            facts = _adapter_facts()
            return _adapter_result(facts)

    deps = SimpleNamespace(agent_loop=Loop(), high_level_goal=object(), space_id="space-a",
                           governed_create_executor=object(), governed_read_executor=object())
    monkeypatch.setattr(harness, "V16OpenDotsArtifactComposition", Composition)
    monkeypatch.setattr(harness, "build_live_dependencies", lambda **_kwargs: deps)
    monkeypatch.setattr("sys.argv", ["agent_opendots_v16_live.py", "--mode", "live-run",
                                      "--run-journal-path", str(tmp_path / "run.jsonl")])
    assert main() == 0
    output = capsys.readouterr().out
    assert 'LIVE_RUN_RESULT={"completed": true' in output
    assert len(calls) == 1


def _adapter_facts(*, nasa=True, esa=True, nasa_text="Artemis I Nov. 16, 2022",
                   esa_text="Rosetta 2 March 2004"):
    facts = []
    if nasa:
        facts.append(DeepReadFact("search_bound_deep_read", "rn", "sn",
                                  NASA_CANONICAL_TARGET, nasa_text, False, "n"))
    if esa:
        facts.append(DeepReadFact("search_bound_deep_read", "re", "se",
                                  ESA_CANONICAL_TARGET, esa_text, False, "e"))
    return facts


def _admitted_facts(facts):
    claims = {NASA_CANONICAL_TARGET: "Nov. 16, 2022", ESA_CANONICAL_TARGET: "2 March 2004"}
    return [AdmittedResearchFact(claims.get(fact.source_url, "synthetic admitted claim"), fact.source_url,
                                 fact.search_execution_id, fact.read_execution_id,
                                 fact.result_digest) for fact in facts]


def _adapter_result(facts, *, admitted=True):
    return SimpleNamespace(deep_read_facts=facts,
                           admitted_facts=_admitted_facts(facts) if admitted else [],
                           status="RESEARCH_HANDOFF", research_handoff=True)


def test_structured_admitted_fact_survives_truncated_excerpt_without_reextraction():
    facts = [
        DeepReadFact("search_bound_deep_read", "read-a", "search-a", NASA_CANONICAL_TARGET,
                     "prefix before the relevant evidence", True, "digest-a"),
        DeepReadFact("search_bound_deep_read", "read-b", "search-b", ESA_CANONICAL_TARGET,
                     "another truncated prefix", True, "digest-b"),
    ]
    result = harness._research_run_from_result(SimpleNamespace(
        deep_read_facts=facts,
        admitted_facts=[
            AdmittedResearchFact("generic claim A", NASA_CANONICAL_TARGET, "search-a", "read-a", "digest-a"),
            AdmittedResearchFact("generic claim B", ESA_CANONICAL_TARGET, "search-b", "read-b", "digest-b"),
        ],
    ))
    assert result is not None
    assert result.chain_a.admitted_fact == "generic claim A"
    assert result.chain_b.admitted_fact == "generic claim B"


def test_structured_admitted_fact_is_required():
    facts = _adapter_facts()
    assert harness._research_run_from_result(_adapter_result(facts, admitted=False)) is None


def test_structured_admitted_fact_binding_mismatch_fails_closed():
    facts = _adapter_facts()
    admitted = _admitted_facts(facts)
    admitted[0] = AdmittedResearchFact("generic claim", NASA_CANONICAL_TARGET,
                                       "wrong-search", "rn", "n")
    assert harness._research_run_from_result(SimpleNamespace(
        deep_read_facts=facts, admitted_facts=admitted
    )) is None


def test_two_structured_admitted_facts_keep_independent_bindings():
    facts = _adapter_facts()
    result = harness._research_run_from_result(_adapter_result(facts))
    assert result is not None
    assert result.chain_a.search_execution_id != result.chain_b.search_execution_id
    assert result.chain_a.read_execution_id != result.chain_b.read_execution_id


def test_research_adapter_diagnostics_record_first_container_rejection_without_repair():
    diagnostics = []
    result = harness._research_run_from_result(
        SimpleNamespace(deep_read_facts=()), diagnostics=diagnostics
    )
    assert result is None
    assert diagnostics == [{
        "checkpoint": "CHECKPOINT_1_DEEP_READ_FACTS_CONTAINER",
        "reason": "deep_read_facts_not_list",
        "observed_type": "tuple",
        "predicate_result": False,
        "expected_type": "list",
    }]


@pytest.mark.parametrize(
    ("facts", "checkpoint"),
    [
        ([], "CHECKPOINT_2_NASA_PUBLISHER_MATCH"),
        (_adapter_facts(nasa_text="not a benchmark fact"), "CHECKPOINT_3_NASA_FACT_ADMISSION"),
        (_adapter_facts(esa=False), "CHECKPOINT_4_ESA_PUBLISHER_MATCH"),
        (_adapter_facts(esa_text="not a benchmark fact"), "CHECKPOINT_5_ESA_FACT_ADMISSION"),
    ],
)
def test_research_adapter_diagnostics_identify_first_rejection_checkpoint(facts, checkpoint):
    diagnostics = []
    result = harness._research_run_from_result(
        (_adapter_result(facts, admitted=False) if checkpoint == "CHECKPOINT_3_NASA_FACT_ADMISSION"
         else SimpleNamespace(deep_read_facts=facts,
                              admitted_facts=(_admitted_facts(facts[:1])
                                              if checkpoint == "CHECKPOINT_5_ESA_FACT_ADMISSION"
                                              else _admitted_facts(facts)))),
        diagnostics=diagnostics
    )
    assert result is None
    assert diagnostics[-1]["checkpoint"] == checkpoint
    assert diagnostics[-1]["predicate_result"] is False
    assert "visible_text" not in diagnostics[-1] or "text" not in diagnostics[-1]


def test_research_adapter_diagnostics_valid_case_has_no_rejection_and_preserves_result():
    facts = _adapter_facts()
    diagnostics = []
    result = harness._research_run_from_result(
        _adapter_result(facts), diagnostics=diagnostics
    )
    assert result is not None
    assert not any(item.get("checkpoint", "").startswith("CHECKPOINT_") and
                   item.get("predicate_result") is False for item in diagnostics)
    assert diagnostics[0]["item_runtime_types"] == ("DeepReadFact", "DeepReadFact")


def test_research_adapter_diagnostics_observes_dict_without_attribute_fallback():
    diagnostics = []
    result = harness._research_run_from_result(
        SimpleNamespace(deep_read_facts=[{"source_url": NASA_CANONICAL_TARGET}], admitted_facts=[]),
        diagnostics=diagnostics,
    )
    assert result is None
    assert diagnostics[-1]["checkpoint"] == "CHECKPOINT_2_NASA_PUBLISHER_MATCH"
    assert diagnostics[0]["item_runtime_types"] == ("dict",)


def test_research_adapter_diagnostics_on_off_preserves_adapter_result():
    facts = _adapter_facts()
    result_off = harness._research_run_from_result(_adapter_result(facts))
    diagnostics = []
    result_on = harness._research_run_from_result(
        _adapter_result(facts), diagnostics=diagnostics
    )
    assert result_off == result_on


def test_research_adapter_diagnostics_never_serializes_secret_content():
    secret = "synthetic-secret-that-must-not-appear"
    diagnostics = []
    result = harness._research_run_from_result(
        _adapter_result(_adapter_facts(nasa_text=secret), admitted=False),
        diagnostics=diagnostics,
    )
    assert result is None
    assert secret not in repr(diagnostics)
    assert diagnostics[-1]["structured_fact_present"] is False
    assert diagnostics[-1]["structured_fact_binding_valid"] is False


def test_research_adapter_rejection_reaches_live_report_without_changing_status():
    class Loop:
        def run(self, _goal):
            return SimpleNamespace(
                status="RESEARCH_HANDOFF", research_handoff=True, deep_read_facts=[], admitted_facts=[]
            )

    diagnostics = []
    result = run_live_operational(SimpleNamespace(
        agent_loop=Loop(), high_level_goal=object(),
        research_adapter_diagnostics_enabled=True,
        research_adapter_diagnostics=diagnostics,
        restore_diagnostics=[],
    ))
    assert result.result.status == "RESEARCH_HANDOFF"
    assert result.research_adapter_diagnostics[-1]["checkpoint"] == (
        "CHECKPOINT_2_NASA_PUBLISHER_MATCH"
    )


def test_research_adapter_diagnostics_off_keeps_default_live_report_shape():
    class Loop:
        def run(self, _goal):
            return SimpleNamespace(
                status="RESEARCH_HANDOFF", research_handoff=True, deep_read_facts=[]
            )

    result = run_live_operational(SimpleNamespace(
        agent_loop=Loop(), high_level_goal=object(), restore_diagnostics=[],
        research_adapter_diagnostics_enabled=False,
        research_adapter_diagnostics=[],
    ))
    assert result.__class__.__name__ == "LiveRunReport"
    assert not hasattr(result, "research_adapter_diagnostics")


def _run_durable_adapter_fixture(tmp_path, *, enabled=True, accepted=False):
    path = tmp_path / "v1_6_research_adapter_durability_v2.jsonl"
    diagnostics = []
    journal = DurableRunJournal(path, "synthetic-adapter-durability-v2")
    facts = _adapter_facts() if accepted else _adapter_facts(
        nasa_text="synthetic-secret-that-must-not-be-persisted"
    )

    class Loop:
        def run(self, _goal):
            return _adapter_result(facts, admitted=accepted)

    try:
        report = run_live_operational(SimpleNamespace(
            agent_loop=Loop(), high_level_goal=object(),
            research_adapter_diagnostics_enabled=enabled,
            research_adapter_diagnostics=diagnostics,
            restore_diagnostics=[], journal=journal,
            space_id="space-a",
            governed_create_executor=SimpleNamespace(arg_names=()),
            governed_read_executor=SimpleNamespace(arg_names=()),
        ))
    finally:
        journal.close()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    ready = next(record for record in records if record["event"] == "RUN_RESULT_READY")
    return report, diagnostics, records, ready, path


def test_research_adapter_diagnostic_survives_fresh_journal_readback(tmp_path):
    report, diagnostics, records, ready, path = _run_durable_adapter_fixture(tmp_path)
    metadata = ready["safe_metadata"]
    durable = metadata["research_adapter_diagnostic"]
    allowed = {
        "first_failing_checkpoint", "first_failing_reason",
        "adapter_input_runtime_type", "deep_read_facts_runtime_type",
        "deep_read_facts_is_list", "deep_read_facts_count", "item_runtime_types",
        "nasa_match_count", "esa_match_count",
        "nasa_matched_item_runtime_type", "esa_matched_item_runtime_type",
        "nasa_source_url_runtime_type", "esa_source_url_runtime_type",
        "nasa_structured_fact_present", "esa_structured_fact_present",
        "nasa_structured_fact_binding_valid", "esa_structured_fact_binding_valid",
        "nasa_extract_result_present", "esa_extract_result_present",
    }
    fresh_records = [json.loads(line) for line in path.read_text().splitlines()]
    fresh_ready = next(record for record in fresh_records if record["event"] == "RUN_RESULT_READY")
    fresh = fresh_ready["safe_metadata"]["research_adapter_diagnostic"]
    assert report.result.status == "RESEARCH_HANDOFF"
    assert diagnostics[-1]["checkpoint"] == "CHECKPOINT_3_NASA_FACT_ADMISSION"
    assert metadata["completed"] is False
    assert metadata["status"] == "RESEARCH_HANDOFF"
    assert set(durable) == allowed
    assert fresh["first_failing_checkpoint"] == "CHECKPOINT_3_NASA_FACT_ADMISSION"
    assert fresh["first_failing_reason"] == "nasa_structured_fact_missing_or_unbound"
    assert fresh["deep_read_facts_runtime_type"] == "list"
    assert fresh["deep_read_facts_count"] == 2
    assert fresh["nasa_match_count"] == 1
    assert fresh["nasa_structured_fact_present"] is False
    assert fresh["nasa_structured_fact_binding_valid"] is False
    assert fresh["nasa_extract_result_present"] is False
    assert all(record["event"] in {"RUN_STARTED", "LOOP_INVOKED", "RUN_RESULT_READY", "TERMINAL_STATUS"}
               for record in records)
    raw = path.read_text()
    assert "synthetic-secret-that-must-not-be-persisted" not in raw
    assert "visible_text_excerpt" not in raw


def test_research_adapter_diagnostics_off_keeps_run_result_ready_shape(tmp_path):
    _report, _diagnostics, _records, ready, _path = _run_durable_adapter_fixture(
        tmp_path, enabled=False
    )
    assert set(ready["safe_metadata"]) == {"completed", "status"}


def test_research_adapter_diagnostics_on_accepted_result_persists_no_rejection(tmp_path):
    _report, _diagnostics, _records, ready, _path = _run_durable_adapter_fixture(
        tmp_path, accepted=True
    )
    assert set(ready["safe_metadata"]) == {"completed", "status"}


def test_research_handoff_preempts_legacy_verifier_after_gate():
    from application.agent_loop import ActionOutcome, AgentLoop
    from application.computer_runtime import ComputerPageObservation
    from application.research_completion import ResearchCompletion
    from application.source_facts import ResearchBudget
    from cognitive.agent_contracts import AgentGoal
    from cognitive.provider import LLMResponse

    class Provider:
        provider_id = "synthetic"

        def complete(self, _prompt):
            return LLMResponse('{"kind":"finish","claim":"done"}', "synthetic", "synthetic")

    class Computer:
        def observe(self, _step):
            return ComputerPageObservation("https://example.test/", "Example", "session", 1, (), "- text: page")

        def act(self, _step, _decision, _observation):
            return ActionOutcome(True, "ok")

    class FlippingPolicy:
        def __init__(self):
            self.calls = 0

        def evaluate(self, _budget, _facts, _deep_facts):
            self.calls += 1
            complete = self.calls >= 2
            publishers = frozenset(("NASA", "ESA")) if complete else frozenset()
            return ResearchCompletion(complete, 2 if complete else 0, publishers,
                                      frozenset(("NASA", "ESA")))

        def prompt_contract(self, _budget):
            return "Research completion is governed by the required publishers."

    def legacy_verifier(_observation):
        raise AssertionError("legacy verifier must not run after research gate")

    result = AgentLoop(
        Provider(), Computer(), legacy_verifier,
        enable_search=True, enable_read=True,
        research=ResearchBudget(target_valid_sources=2, max_search_attempts=4, max_deep_read_attempts=4),
        research_completion_policy=FlippingPolicy(),
        stop_after_research_gate=True,
        trace_sink=MemoryExecutionTrace("handoff-order"),
    ).run(AgentGoal("handoff", "research", max_steps=2))

    assert result.status == "RESEARCH_HANDOFF"
    assert result.research_handoff is True
    assert result.research_state["research_complete"] is True
    assert result.verification is None


@dataclass(frozen=True)
class _UnicodeLiveResult:
    message: str
    nested: dict[str, str]


@pytest.mark.parametrize("encoding", ["ascii", "utf-8", "cp1252"])
def test_live_result_serialization_is_terminal_encoding_safe(encoding):
    result = _UnicodeLiveResult("A → B ✓ — “quoted”", {"content": "Artemis I → Rosetta"})
    serialized = harness._serialize_live_result(result)
    encoded = serialized.encode(encoding)
    assert encoded
    assert "\\u2192" in serialized and "\\u2713" in serialized
    assert "Artemis I" in serialized and "Rosetta" in serialized


def test_live_cli_serializes_unicode_result_after_one_runner_call(monkeypatch, tmp_path):
    result = _UnicodeLiveResult("A → B", {"content": "Artemis I — Rosetta"})
    calls = []
    monkeypatch.setattr(harness, "dispatch_mode", lambda mode, **_kwargs: calls.append(mode) or result)
    monkeypatch.setattr(sys, "argv", ["agent_opendots_v16_live.py", "--mode", "live-run",
                                        "--run-journal-path", str(tmp_path / "run.jsonl")])
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    try:
        assert harness.main() == 0
        stream.flush()
        output = raw.getvalue().decode("cp1252")
    finally:
        stream.detach()
    assert calls == ["live-run"]
    assert "LIVE_RUN_RESULT=" in output
    assert "\\u2192" in output and "\\u2014" in output


def test_live_cli_reports_unserializable_result_without_second_runner_call(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(harness, "dispatch_mode", lambda mode, **_kwargs: calls.append(mode) or object())
    monkeypatch.setattr(sys, "argv", ["agent_opendots_v16_live.py", "--mode", "live-run",
                                        "--run-journal-path", str(tmp_path / "run.jsonl")])
    assert harness.main() == 1
    assert calls == ["live-run"]
    assert "LIVE_RUN_RESULT_SERIALIZATION_FAILURE" in capsys.readouterr().out
