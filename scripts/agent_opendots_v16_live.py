"""Synthetic preflight harness for the V1.6 OpenDots series.

This module deliberately does not start OpenDots, create an HTTP client, or
run the live AgentLoop.  It composes the already-governed OpenDots artifact
ports with the proven V1.6 research/completion path so the future live
entrypoint has a small, auditable contract to implement.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import os
import secrets
import shutil
import time
import tempfile
import re
from pathlib import Path
import socket
import subprocess
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from application.deep_read_transport_trace import deep_read_operation, make_traced_transport
from application.agent_runtime import ResearchReturnContext
from application.computer_runtime import ComputerRuntimeError, OpenBotComputerAdapter
from application.daily_brief import configured_agnes_provider
from application.execution_trace import MemoryExecutionTrace
from application.v16_independent_publisher import (
    ArtifactObservation,
    ESA,
    NASA,
    PublisherChain,
    V16OpenDotsArtifactComposition,
    V16OpenDotsCompositionResult,
    V16SyntheticRun,
    make_synthetic_run,
    verify_v16,
)
from application.source_facts import AdmittedResearchFact
from governance.opendots import ArtifactReceipt
from governance.aep_executors import BraveSearchExecutor, opendots_executors
from governance.bound_read import canonical_source_url
from scripts.agent_todomvc_e2e import AGNES_KEYS, COMPUTER_KEYS, read_env
from scripts.agent_wombat_v16_live import (
    GOAL,
    PUBLISHER_POLICY,
    build_live_v16_loop,
    validate_live_configuration,
)


TITLE = "Space Mission Launch Comparison"
CONTENT = "Artemis I — Nov. 16, 2022\nRosetta — 2 March 2004"


@dataclass(frozen=True)
class OpenDotsStartupFailureEvidence:
    process_exit_code: int | None
    stdout_present: bool
    stderr_present: bool
    stdout_last_safe_lines: tuple[str, ...]
    stderr_last_safe_lines: tuple[str, ...]
    first_fatal_error_class: str
    first_fatal_error_message_safe: str | None
    stacktrace_present: bool
    stacktrace_first_relevant_frame: str | None
    database_file_existed_before_cleanup: bool
    database_size_bytes_before_cleanup: int | None
    http_listener_observed: bool
    failure_stage: str


class OpenDotsHarnessPreflightError(RuntimeError):
    """Raised when the deterministic synthetic preflight cannot proceed."""

    def __init__(self, message: str, *, startup_failure_evidence: OpenDotsStartupFailureEvidence | None = None):
        super().__init__(message)
        self.startup_failure_evidence = startup_failure_evidence


_STARTUP_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:basic|bearer)\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:owner[_-]?token|agnes[_-]?api[_-]?key|computer[_-]?token|proxy[_-]?(?:auth|password|credential)|cookie)\s*[:=]\s*)[^\s,;]+"),
)


def _sanitize_startup_text(text: str, secrets_to_redact: Sequence[str]) -> str:
    sanitized = text
    for secret in secrets_to_redact:
        if secret:
            sanitized = sanitized.replace(secret, "<REDACTED>")
    for pattern in _STARTUP_SECRET_PATTERNS:
        sanitized = pattern.sub(r"\1<REDACTED>", sanitized)
    return sanitized


def _startup_log_lines(path: Path, secrets_to_redact: Sequence[str]) -> tuple[bool, tuple[str, ...], str]:
    if not path.is_file():
        return False, (), ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True, (), ""
    sanitized = _sanitize_startup_text(text, secrets_to_redact)
    lines = tuple(line[:500] for line in sanitized.splitlines()[-20:])
    return True, lines, sanitized


def _startup_failure_evidence(*, process: subprocess.Popen[str], stdout_path: Path,
                              stderr_path: Path, child_env: Mapping[str, str],
                              failure_stage: str, http_listener_observed: bool = False,
                              process_exit_code: int | None = None
                              ) -> OpenDotsStartupFailureEvidence:
    secrets_to_redact = tuple(child_env.get(key, "") for key in (
        "OWNER_TOKEN", "AGNES_API_KEY", "COMPUTER_TOKEN", "BROWSER_SECRET",
        "COMPUTER_SUPERVISOR_TOKEN", "PARALLEL_API_KEY", "OPENAI_API_KEY",
    ))
    stdout_present, stdout_lines, stdout_text = _startup_log_lines(stdout_path, secrets_to_redact)
    stderr_present, stderr_lines, stderr_text = _startup_log_lines(stderr_path, secrets_to_redact)
    combined = (*stderr_lines, *stdout_lines)
    fatal_line = next((line for line in combined if re.search(
        r"(?i)(fatal|error|exception|abort|assert|traceback)", line)), None)
    fatal_class = "UNKNOWN"
    if fatal_line:
        if re.search(r"(?i)traceback|exception", fatal_line):
            fatal_class = "EXCEPTION"
        elif re.search(r"(?i)assert", fatal_line):
            fatal_class = "ASSERTION"
        elif re.search(r"(?i)abort", fatal_line):
            fatal_class = "ABORT"
        else:
            fatal_class = "ERROR"
    stack_frame = next((line.strip() for line in combined if re.search(
        r"(?i)(^\s*at\s+|traceback|file \".*\", line)", line)), None)
    database_path = child_env.get("DATABASE_PATH", "")
    database = Path(database_path) if database_path else None
    database_exists = bool(database and database.is_file())
    database_size = database.stat().st_size if database_exists else None
    return OpenDotsStartupFailureEvidence(
        process_exit_code=(process.poll() if process_exit_code is None else process_exit_code),
        stdout_present=stdout_present,
        stderr_present=stderr_present,
        stdout_last_safe_lines=stdout_lines,
        stderr_last_safe_lines=stderr_lines,
        first_fatal_error_class=fatal_class,
        first_fatal_error_message_safe=fatal_line,
        stacktrace_present=stack_frame is not None or bool(re.search(r"(?i)traceback|stack", stdout_text + stderr_text)),
        stacktrace_first_relevant_frame=stack_frame,
        database_file_existed_before_cleanup=database_exists,
        database_size_bytes_before_cleanup=database_size,
        http_listener_observed=http_listener_observed,
        failure_stage=failure_stage,
    )


def _startup_failure_evidence_dict(exc: BaseException) -> dict[str, Any] | None:
    evidence = getattr(exc, "startup_failure_evidence", None)
    if evidence is None:
        return None
    return asdict(evidence)


def _print_startup_failure_evidence(exc: BaseException) -> None:
    evidence = _startup_failure_evidence_dict(exc)
    if evidence is not None:
        print("STARTUP_FAILURE_EVIDENCE=" + json.dumps(evidence, ensure_ascii=True, sort_keys=True))


class DurableRunJournal:
    """Small append-only journal whose records are durable before returning."""

    def __init__(self, path: str | Path, run_id: str):
        candidate = Path(path)
        if not candidate.is_absolute():
            raise OpenDotsHarnessPreflightError("run_journal_path_must_be_absolute")
        self.path = candidate.resolve()
        self.run_id = run_id
        self._seq = 0
        self._finalized = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self.path.open("a", encoding="utf-8", newline="\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except Exception as exc:
            try:
                self._handle.close()
            except Exception:
                pass
            raise OpenDotsHarnessPreflightError("run_journal_unavailable") from exc

    def append(self, event: str, **metadata: Any) -> None:
        record = {
            "run_id": self.run_id,
            "seq": self._seq + 1,
            "timestamp": time.time(),
            "event": event,
            "safe_metadata": dict(metadata),
        }
        line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
        self._handle.write(line)
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._seq += 1
        if event == "TERMINAL_STATUS":
            self._finalized = True

    def close(self) -> None:
        self._handle.close()


class DurableTraceSink:
    """Forward the existing trace while durably retaining approved diagnostics."""

    _RESTORE_FIELDS = (
        "requested_return_url", "navigation_attempted", "navigation_succeeded",
        "navigation_final_url", "observation_attempted", "observation_succeeded",
        "observed_url", "nav_url_matches_return", "observed_url_matches_return",
        "failure_substage",
    )
    _ACTION_FAILURE_FIELDS = (
        "action", "tool_kind", "result", "failure_code", "safe_exception_class",
        "failure_counter_before", "failure_counter_after", "configured_failure_threshold",
        "too_many_action_failures_triggered",
    )
    _CLAIM_ADMISSION_FAILURE_FIELDS = ("failure_code", "failure_stage")
    _PROVENANCE_FIELDS = (
        "stage", "iteration_id", "action_id", "model_observation", "model_tool_contract",
        "raw_model_output", "parsed_action", "parsed_args", "validation_input",
        "validation_result", "failure_code", "feedback_returned_to_model", "pending_feedback",
    )

    def __init__(self, inner: Any, journal: DurableRunJournal):
        self.inner = inner
        self.journal = journal

    @property
    def events(self):
        return self.inner.events

    def emit(self, event: Any, **fields: Any) -> None:
        self.inner.emit(event, **fields)
        event_type = event.get("event_type") if isinstance(event, Mapping) else event
        if event_type == "RESTORE_DIAGNOSTIC":
            self.journal.append(
                "RESTORE_DIAGNOSTIC",
                **{key: event.get(key) for key in self._RESTORE_FIELDS},
            )
        elif event_type in {"ACTION_RESULT", "SEARCH_RESULT", "DEEP_READ_RESULT"} \
                and event.get("result") == "FAIL":
            self.journal.append(
                "ACTION_FAILURE",
                **{key: event.get(key) for key in self._ACTION_FAILURE_FIELDS},
            )
        elif event_type == "RESEARCH_CLAIM_ADMISSION_FAILURE":
            self.journal.append(
                "RESEARCH_CLAIM_ADMISSION_FAILURE",
                **{key: event.get(key) for key in self._CLAIM_ADMISSION_FAILURE_FIELDS},
            )
        elif event_type == "AGENTLOOP_PROVENANCE":
            self.journal.append(
                "AGENTLOOP_PROVENANCE",
                **{key: event.get(key) for key in self._PROVENANCE_FIELDS if key in event},
            )


def read_durable_run_journal(path: str | Path) -> dict[str, Any]:
    """Recover durable state, tolerating only a truncated final JSONL record."""
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        lines = handle.readlines()
    for index, line in enumerate(lines):
        if not line.endswith("\n"):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    break
                raise
            continue
        records.append(json.loads(line))
    state: dict[str, Any] = {
        "run_started": False, "jit_status": None, "loop_invoked": False,
        "attempt_consumed": None, "last_durable_event": None,
        "terminal_status": None, "restore_diagnostics_count": 0,
        "action_failure_sequence": [], "failed_action_counts_by_kind": {},
        "failure_counter_transitions": [], "too_many_action_failures_trigger": None,
        "research_claim_admission_failures": [], "agentloop_provenance": [],
    }
    for record in records:
        state["last_durable_event"] = record.get("event")
        event = record.get("event")
        metadata = record.get("safe_metadata") or {}
        if event == "RUN_STARTED":
            state["run_started"] = True
        elif event == "JIT_STARTED":
            state["jit_status"] = "STARTED"
        elif event == "JIT_PASSED":
            state["jit_status"] = "PASSED"
        elif event == "JIT_FAILED":
            state["jit_status"] = "FAILED"
        elif event == "LOOP_INVOKED":
            state["loop_invoked"] = True
        elif event == "CONSUMPTION_BOUNDARY":
            state["attempt_consumed"] = "YES"
        elif event == "RESTORE_DIAGNOSTIC":
            state["restore_diagnostics_count"] += 1
        elif event == "ACTION_FAILURE":
            failure = dict(metadata)
            state["action_failure_sequence"].append(failure)
            kind = failure.get("tool_kind") or failure.get("action") or "unknown"
            counts = state["failed_action_counts_by_kind"]
            counts[kind] = counts.get(kind, 0) + 1
            state["failure_counter_transitions"].append({
                "before": failure.get("failure_counter_before"),
                "after": failure.get("failure_counter_after"),
                "threshold": failure.get("configured_failure_threshold"),
                "kind": kind,
            })
            if failure.get("too_many_action_failures_triggered") is True:
                state["too_many_action_failures_trigger"] = failure
        elif event == "RESEARCH_CLAIM_ADMISSION_FAILURE":
            state["research_claim_admission_failures"].append(dict(metadata))
        elif event == "AGENTLOOP_PROVENANCE":
            state["agentloop_provenance"].append(dict(metadata))
        elif event == "TERMINAL_STATUS":
            state["terminal_status"] = metadata.get("status")
    if state["attempt_consumed"] is None:
        if state["jit_status"] == "FAILED" and not state["loop_invoked"]:
            state["attempt_consumed"] = "NO"
        elif state["loop_invoked"]:
            state["attempt_consumed"] = "UNKNOWN"
    return state


OPENDOTS_STARTUP_TIMEOUT_MS = 30_000
OPENDOTS_HOST = "127.0.0.1"
OPENDOTS_PORT = 4310
OPENDOTS_STARTUP_COMMAND = (
    "node", "--env-file-if-exists=.env", "dist/server/server/index.js"
)
OPENDOTS_ROOT = Path("D:/opendots-v16")
MANAGED_OPENDOTS_BASE_URL = f"http://{OPENDOTS_HOST}:{OPENDOTS_PORT}"


@dataclass
class OpenDotsProcess:
    """Owned OpenDots process and diagnostic files for one operational run."""

    process: subprocess.Popen[str]
    stdout_path: Path
    stderr_path: Path

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


@dataclass
class OpenDotsRuntimeLease:
    """Runtime configuration plus explicit ownership for one OpenDots process."""

    config: LiveRuntimeConfig
    process: OpenDotsProcess | None = None
    cleanup_dir: Path | None = None

    @property
    def owned(self) -> bool:
        return self.process is not None

    def close(self) -> None:
        if self.process is not None:
            self.process.stop()
        if self.cleanup_dir is not None:
            shutil.rmtree(self.cleanup_dir, ignore_errors=True)


def _opendots_entrypoint(root: str | Path) -> Path:
    return Path(root) / "dist" / "server" / "server" / "index.js"


def wait_for_opendots_listener(*, host: str = OPENDOTS_HOST,
                               port: int = OPENDOTS_PORT,
                               timeout_ms: int = OPENDOTS_STARTUP_TIMEOUT_MS,
                               sleep_ms: int = 250,
                               process: subprocess.Popen[str] | None = None) -> None:
    """Wait only for process startup; API readiness is checked afterwards."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise OpenDotsHarnessPreflightError(
                f"opendots_process_exited:{process.returncode}"
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(sleep_ms / 1000)
    raise OpenDotsHarnessPreflightError("opendots_listener_timeout")


def start_opendots_runtime(*, root: str | Path,
                           env: Mapping[str, str] | None = None,
                           stdout_path: str | Path,
                           stderr_path: str | Path,
                           host: str = OPENDOTS_HOST,
                           port: int = OPENDOTS_PORT,
                           timeout_ms: int = OPENDOTS_STARTUP_TIMEOUT_MS) -> OpenDotsProcess:
    """Start the compiled server using the shared preflight/live contract."""
    root_path = Path(root)
    entrypoint = _opendots_entrypoint(root_path)
    if not entrypoint.is_file():
        raise OpenDotsHarnessPreflightError("compiled_opendots_entrypoint_missing")
    child_env = dict(os.environ if env is None else env)
    child_env.update({"HOST": host, "PORT": str(port)})
    stdout_file = Path(stdout_path)
    stderr_file = Path(stderr_path)
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)
    stdout = stdout_file.open("w", encoding="utf-8", errors="replace")
    stderr = stderr_file.open("w", encoding="utf-8", errors="replace")
    try:
        process = subprocess.Popen(
            list(OPENDOTS_STARTUP_COMMAND), cwd=root_path, env=child_env,
            stdout=stdout, stderr=stderr, text=True,
        )
    except Exception:
        stdout.close()
        stderr.close()
        raise
    stdout.close()
    stderr.close()
    runtime = OpenDotsProcess(process, stdout_file, stderr_file)
    try:
        wait_for_opendots_listener(host=host, port=port, timeout_ms=timeout_ms,
                                   process=process)
    except Exception as exc:
        original_exit_code = process.poll()
        runtime.stop()
        evidence = _startup_failure_evidence(
            process=process,
            stdout_path=stdout_file,
            stderr_path=stderr_file,
            child_env=child_env,
            failure_stage=("startup_process_exit" if original_exit_code is not None
                            else "startup_readiness_timeout"),
            process_exit_code=original_exit_code,
        )
        if isinstance(exc, OpenDotsHarnessPreflightError):
            exc.startup_failure_evidence = evidence
            raise
        raise OpenDotsHarnessPreflightError(
            str(exc), startup_failure_evidence=evidence
        ) from exc
    return runtime


def _port_is_occupied(host: str = OPENDOTS_HOST, port: int = OPENDOTS_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def prepare_opendots_runtime(*, env: Mapping[str, str] | None = None,
                             root: str | Path = OPENDOTS_ROOT,
                             stdout_path: str | Path | None = None,
                             stderr_path: str | Path | None = None,
                             starter: Callable[..., OpenDotsProcess] = start_opendots_runtime,
                             port_occupied: Callable[[], bool] = _port_is_occupied) -> tuple[OpenDotsRuntimeLease, dict[str, str]]:
    """Resolve external config or own a disposable runtime with one in-memory token."""
    values = dict(os.environ if env is None else env)
    base_url = values.get("OPENDOTS_BASE_URL", "").strip()
    owner_token = values.get("OWNER_TOKEN", "").strip()
    if bool(base_url) != bool(owner_token):
        raise OpenDotsHarnessPreflightError("opendots_runtime_config_partial")
    if base_url and owner_token:
        return OpenDotsRuntimeLease(LiveRuntimeConfig(base_url, owner_token)), values

    if port_occupied():
        raise OpenDotsHarnessPreflightError("managed_opendots_port_occupied")
    token = secrets.token_urlsafe(32)
    runtime_dir = Path(tempfile.mkdtemp(prefix="iaos-opendots-"))
    stdout = Path(stdout_path) if stdout_path is not None else runtime_dir / "stdout.log"
    stderr = Path(stderr_path) if stderr_path is not None else runtime_dir / "stderr.log"
    child_env = dict(values)
    child_env.update({
        "HOST": OPENDOTS_HOST,
        "PORT": str(OPENDOTS_PORT),
        "OWNER_TOKEN": token,
        "DATABASE_PATH": str(runtime_dir / "opendots.sqlite"),
        "DO_NOT_TRACK": "1",
        "WEB_SEARCH_PROVIDER": "disabled",
    })
    try:
        process = starter(root=root, env=child_env, stdout_path=stdout, stderr_path=stderr)
    except Exception:
        shutil.rmtree(runtime_dir, ignore_errors=True)
        raise
    runtime_env = dict(values)
    runtime_env.update({"OPENDOTS_BASE_URL": MANAGED_OPENDOTS_BASE_URL, "OWNER_TOKEN": token})
    return OpenDotsRuntimeLease(
        LiveRuntimeConfig(MANAGED_OPENDOTS_BASE_URL, token), process, runtime_dir,
    ), runtime_env


@dataclass(frozen=True)
class BoundSpace:
    space_id: str


@dataclass
class HarnessTrace:
    """Secret-free, ordered trace for the synthetic harness."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})


@dataclass(frozen=True)
class HarnessCounters:
    search_attempts: int
    deep_read_attempts: int
    admitted_sources: int
    publishers: tuple[str, ...]
    page_creates: int
    page_reads: int
    page_edits: int
    wombat_actions: int = 0


@dataclass(frozen=True)
class SyntheticHarnessResult:
    completed: bool
    preflight: BoundSpace | None
    composition: V16OpenDotsCompositionResult | None
    trace: HarnessTrace
    counters: HarnessCounters
    failure_reason: str | None = None


@dataclass(frozen=True)
class LiveRuntimeConfig:
    """Runtime-only configuration; secrets never enter proposals or traces."""

    base_url: str
    owner_token: str


@dataclass(frozen=True)
class LivePreflightResult:
    passed: bool
    bound_space: BoundSpace | None
    state_status: int | None
    workspace_status: int | None
    openbot_healthy: bool
    research_ready: bool
    target_policy_ready: bool
    failure_reason: str | None = None
    observation_ready: bool = True
    snapshot_status: int | None = None
    run_status: int | None = None
    snapshot_shape_valid: bool | None = None
    aria_shape_valid: bool | None = None
    observed_page_url_present: bool | None = None
    observed_page_url_canonicalizable: bool | None = None
    observation_exception_type: str | None = None
    observation_exception_message: str | None = None
    observation_exception_cause: str | None = None
    observation_probe_attempted: bool = False
    navigation_readiness_attempts: int = 0
    navigation_readiness_http_status: int | None = None
    navigation_readiness_succeeded: bool = False
    post_nav_observation_attempted: bool = False
    post_nav_observation_succeeded: bool = False
    post_nav_page_url: str | None = None


@dataclass(frozen=True)
class LiveDependencies:
    """All runtime objects needed before the first model/effect boundary."""

    model_runtime: Any
    research_runtime: Any
    openbot_runtime: Any
    opendots_config: LiveRuntimeConfig
    governed_create_executor: Any
    governed_read_executor: Any
    research_policy: Any
    structured_verifier: Any
    trace_sink: Any
    agent_loop: Any
    high_level_goal: Any
    space_id: str | None = None
    restore_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    journal: DurableRunJournal | None = None
    research_adapter_diagnostics_enabled: bool = False
    research_adapter_diagnostics: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class LiveRunReport:
    """Live result plus side-channel restore diagnostics; no decision data is added."""

    result: Any
    restore_diagnostics: tuple[Mapping[str, Any], ...]
    restore_diagnostics_count: int
    restore_failure_diagnostics_count: int


@dataclass(frozen=True)
class LiveRunReportWithResearchAdapterDiagnostics(LiveRunReport):
    """Opt-in extension; the default live report shape remains unchanged."""

    research_adapter_diagnostics: tuple[Mapping[str, Any], ...] = ()


def _record_restore_diagnostic(trace: Any, collector: list[dict[str, Any]], event: Mapping[str, Any]) -> None:
    """Retain only the adapter's already-approved restore diagnostic fields."""
    allowed = (
        "requested_return_url", "navigation_attempted", "navigation_succeeded",
        "navigation_final_url", "observation_attempted", "observation_succeeded",
        "observed_url", "nav_url_matches_return", "observed_url_matches_return",
        "failure_substage",
    )
    record = {"restore_diagnostic_index": len(collector) + 1}
    record.update({key: event.get(key) for key in allowed})
    collector.append(record)
    trace.emit({"event_type": "RESTORE_DIAGNOSTIC", **record})


def _configured_values(keys: Sequence[str], *, file_env: str,
                       values_source: Mapping[str, str]) -> dict[str, str]:
    values = {key: values_source[key] for key in keys if values_source.get(key, "").strip()}
    if len(values) != len(keys):
        path = values_source.get(file_env, "").strip()
        if path:
            values = read_env(path, tuple(keys))
    return values


def build_live_dependencies(*, env: Mapping[str, str] | None = None,
                            computer: dict[str, str] | None = None,
                            agnes: dict[str, str] | None = None,
                            brave: dict[str, str] | None = None,
                            trace_sink: Any | None = None,
                            provider_factory: Callable[[], Any] | None = None,
                            computer_factory: Callable[..., Any] | None = None,
                            search_factory: Callable[[], Any] | None = None,
                            port: Any | None = None,
                            journal: DurableRunJournal | None = None,
                            research_adapter_diagnostics: bool = False) -> LiveDependencies:
    """Compose existing live runtimes without invoking model or effect calls."""
    values = os.environ if env is None else env
    config = resolve_runtime_config(values)
    computer = computer or _configured_values(COMPUTER_KEYS, file_env="COMPUTER_ENV_FILE",
                                              values_source=values)
    agnes = agnes or _configured_values(AGNES_KEYS, file_env="AGNES_ENV_FILE",
                                        values_source=values)
    brave = brave or _configured_values(("BRAVE_SEARCH_API_KEY",), file_env="AGNES_ENV_FILE",
                                        values_source=values)
    validate_live_configuration(computer, agnes, brave)
    trace = trace_sink or MemoryExecutionTrace("v1-6-opendots-boundary")
    if journal is not None and not isinstance(trace, DurableTraceSink):
        trace = DurableTraceSink(trace, journal)
    restore_diagnostics: list[dict[str, Any]] = []
    if port is None and (computer_factory or OpenBotComputerAdapter) is OpenBotComputerAdapter:
        port = OpenBotComputerAdapter(
            computer["IAOS_COMPUTER_URL"], computer["IAOS_COMPUTER_BOT_ID"],
            computer["IAOS_COMPUTER_TOKEN"],
            transport=make_traced_transport(trace),
            restore_diagnostic_sink=lambda event: _record_restore_diagnostic(trace, restore_diagnostics, event),
        )
    loop, session, provider, search, runtime_port = build_live_v16_loop(
        computer, agnes, brave, provider_factory=provider_factory or configured_agnes_provider,
        computer_factory=computer_factory or OpenBotComputerAdapter,
        search_factory=search_factory or BraveSearchExecutor,
        port=port, trace_sink=trace, stop_after_research_gate=True,
    )
    if journal is not None:
        loop._consumption_boundary_sink = lambda event: journal.append(
            "CONSUMPTION_BOUNDARY", kind=event.kind, ordinal=event.ordinal, turn=event.turn
        )
    executors = opendots_executors(config.base_url, config.owner_token)
    return LiveDependencies(
        model_runtime=provider,
        research_runtime=(loop, session, search),
        openbot_runtime=runtime_port,
        opendots_config=config,
        governed_create_executor=executors["opendots.page.create"],
        governed_read_executor=executors["opendots.page.read"],
        research_policy=PUBLISHER_POLICY,
        structured_verifier=verify_v16,
        trace_sink=trace,
        agent_loop=loop,
        high_level_goal=GOAL,
        restore_diagnostics=restore_diagnostics,
        journal=journal,
        research_adapter_diagnostics_enabled=research_adapter_diagnostics,
    )


LIVE_MODES = ("synthetic-preflight", "live-preflight", "live-run")
LIVE_RUN_PAGE_CREATE_TRANSPORT = "IAOS_COMMITTED_EXECUTOR_ONLY"
LIVE_RUN_PAGE_READ_TRANSPORT = "IAOS_COMMITTED_EXECUTOR_ONLY"
NASA_CANONICAL_TARGET = "https://www.nasa.gov/mission/artemis-i/"
ESA_CANONICAL_TARGET = "https://www.esa.int/Science_Exploration/Space_Science/Rosetta"
RESEARCH_RETURN_ANCHOR = NASA_CANONICAL_TARGET


def resolve_runtime_config(env: Mapping[str, str] | None = None) -> LiveRuntimeConfig:
    """Resolve the existing executor contract without printing or persisting secrets."""
    values = os.environ if env is None else env
    base_url = values.get("OPENDOTS_BASE_URL", "").strip()
    owner_token = values.get("OWNER_TOKEN", "").strip()
    if not base_url:
        raise OpenDotsHarnessPreflightError("missing_opendots_base_url")
    if not owner_token:
        raise OpenDotsHarnessPreflightError("missing_owner_token")
    return LiveRuntimeConfig(base_url, owner_token)


def _default_readonly_get(config: LiveRuntimeConfig, path: str) -> tuple[int, Any]:
    request = Request(config.base_url.rstrip("/") + path,
                      headers={"Authorization": "Bearer " + config.owner_token,
                               "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed GET-only control plane
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except Exception as exc:
        status = getattr(getattr(exc, "code", None), "real", None)
        return (status if isinstance(status, int) else 0), None


def _openbot_health_check(computer: Mapping[str, str]) -> bool:
    """Check the configured OpenBot health contract without creating a session."""
    endpoint = computer.get("IAOS_COMPUTER_URL", "").strip().rstrip("/")
    bot_id = computer.get("IAOS_COMPUTER_BOT_ID", "").strip()
    token = computer.get("IAOS_COMPUTER_TOKEN", "").strip()
    if not endpoint or not bot_id or not token:
        return False
    request = Request(
        endpoint + "/health",
        headers={
            "x-openbot-computer-token": token,
            "x-openbot-bot-id": bot_id,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed health GET
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return isinstance(payload, dict) and payload.get("status") == "ok"
    except Exception:
        return False


def _openbot_observation_probe(computer: Mapping[str, str]) -> dict[str, Any]:
    """Exercise observe, one readiness navigation, and post-navigation observe."""
    endpoint = computer.get("IAOS_COMPUTER_URL", "").strip()
    bot_id = computer.get("IAOS_COMPUTER_BOT_ID", "").strip()
    token = computer.get("IAOS_COMPUTER_TOKEN", "").strip()
    if not endpoint or not bot_id or not token:
        return {"ready": False, "exception_type": "RuntimeError",
                "exception_message": "computer_observation_config_missing"}
    trace = MemoryExecutionTrace("preflight-observation")
    adapter = OpenBotComputerAdapter(
        endpoint, bot_id, token, transport=make_traced_transport(trace)
    )
    try:
        with deep_read_operation("preflight-observation", "observation_probe"):
            adapter.observe()
        results = {
            event.get("endpoint"): event.get("http_status")
            for event in trace.events if event.get("event_type") == "COMPUTER_HTTP_RESULT"
        }
        navigation_attempts = 1
        try:
            final_url = adapter.navigate(NASA_CANONICAL_TARGET)
        except Exception as exc:
            results = {
                event.get("endpoint"): event.get("http_status")
                for event in trace.events if event.get("event_type") == "COMPUTER_HTTP_RESULT"
            }
            return {
                "ready": False,
                "snapshot_status": results.get("/snapshot"),
                "run_status": results.get("/run"),
                "snapshot_shape_valid": True,
                "aria_shape_valid": True,
                "page_url_present": True,
                "page_url_canonicalizable": False,
                "navigation_readiness_attempts": navigation_attempts,
                "navigation_readiness_http_status": results.get("/navigate"),
                "navigation_readiness_succeeded": False,
                "post_nav_observation_attempted": False,
                "post_nav_observation_succeeded": False,
                "post_nav_page_url": None,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:500],
            }
        with deep_read_operation("preflight-observation", "post_navigation_observation"):
            post_observation = adapter.observe()
        results = {
            event.get("endpoint"): event.get("http_status")
            for event in trace.events if event.get("event_type") == "COMPUTER_HTTP_RESULT"
        }
        page_url = post_observation.page_url
        parsed = urlparse(page_url)
        return {
            "ready": True,
            "snapshot_status": results.get("/snapshot"),
            "run_status": results.get("/run"),
            "snapshot_shape_valid": True,
            "aria_shape_valid": isinstance(post_observation.aria_snapshot, str),
            "page_url_present": bool(page_url),
            "page_url_canonicalizable": bool(parsed.scheme and parsed.hostname),
            "navigation_readiness_attempts": navigation_attempts,
            "navigation_readiness_http_status": results.get("/navigate"),
            "navigation_readiness_succeeded": True,
            "post_nav_observation_attempted": True,
            "post_nav_observation_succeeded": True,
            "post_nav_page_url": page_url,
        }
    except Exception as exc:
        cause = exc.__cause__
        return {
            "ready": False,
            "snapshot_status": next((e.get("http_status") for e in trace.events
                                      if e.get("event_type") == "COMPUTER_HTTP_RESULT"
                                      and e.get("endpoint") == "/snapshot"), None),
            "run_status": next((e.get("http_status") for e in trace.events
                                 if e.get("event_type") == "COMPUTER_HTTP_RESULT"
                                 and e.get("endpoint") == "/run"), None),
            "snapshot_shape_valid": next((e.get("response_shape_valid") for e in trace.events
                                           if e.get("event_type") == "COMPUTER_HTTP_RESULT"
                                           and e.get("endpoint") == "/snapshot"), None),
            "exception_type": type(exc).__name__,
            "exception_message": str(exc)[:500],
            "exception_cause": (type(cause).__name__ + ": " + str(cause)[:500]) if cause else None,
            "navigation_readiness_attempts": 0,
            "navigation_readiness_http_status": None,
            "navigation_readiness_succeeded": False,
            "post_nav_observation_attempted": False,
            "post_nav_observation_succeeded": False,
            "post_nav_page_url": None,
        }


def live_preflight(*, env: Mapping[str, str] | None = None,
                   readonly_get: Callable[[LiveRuntimeConfig, str], tuple[int, Any]] | None = None,
                   openbot_health: Callable[[], bool] | None = None,
                   observation_probe: Callable[[], Any] | None = None,
                   research_config_ready: bool = True,
                   target_policy: Mapping[str, str] | None = None) -> LivePreflightResult:
    """Perform health/config checks only; no model, search, deep-read, or Page effect."""
    try:
        config = resolve_runtime_config(env)
    except OpenDotsHarnessPreflightError as exc:
        return LivePreflightResult(False, None, None, None, False, False, False, str(exc),
                                   observation_ready=False)
    get = readonly_get or _default_readonly_get
    state_status, _state = get(config, "/api/state")
    if state_status != 200:
        return LivePreflightResult(False, None, state_status, None, False, False, False,
                                   "opendots_state_not_ready", observation_ready=False)
    workspace_status, workspace = get(config, "/api/workspace")
    if workspace_status != 200 or not isinstance(workspace, dict):
        return LivePreflightResult(False, None, state_status, workspace_status, False, False, False,
                                   "opendots_workspace_not_ready", observation_ready=False)
    spaces = workspace.get("spaces")
    if not isinstance(spaces, list) or len(spaces) != 1:
        return LivePreflightResult(False, None, state_status, workspace_status, False, False, False,
                                   "space_count_must_equal_one", observation_ready=False)
    space_id = spaces[0].get("id") if isinstance(spaces[0], dict) else None
    if not isinstance(space_id, str) or not space_id:
        return LivePreflightResult(False, None, state_status, workspace_status, False, False, False,
                                   "space_id_invalid", observation_ready=False)
    target = target_policy or {"NASA": NASA_CANONICAL_TARGET, "ESA": ESA_CANONICAL_TARGET}
    target_ready = target == {"NASA": NASA_CANONICAL_TARGET, "ESA": ESA_CANONICAL_TARGET}
    healthy = bool(openbot_health()) if openbot_health is not None else False
    observation_probe_attempted = observation_probe is not None
    probe = observation_probe() if observation_probe is not None else {"ready": True}
    if isinstance(probe, bool):
        probe = {"ready": probe}
    observation_ready = bool(probe.get("ready"))
    navigation_ready = bool(probe.get("navigation_readiness_succeeded"))
    post_nav_ready = bool(probe.get("post_nav_observation_succeeded"))
    passed = (healthy and observation_ready and navigation_ready and post_nav_ready
              and research_config_ready and target_ready)
    reason = None if passed else ("openbot_unhealthy" if not healthy else
                                 "observation_not_ready" if not observation_ready else
                                 "navigation_readiness_failed" if not navigation_ready else
                                 "post_navigation_observation_not_ready" if not post_nav_ready else
                                 "research_configuration_missing" if not research_config_ready else
                                 "target_policy_mismatch")
    return LivePreflightResult(passed, BoundSpace(space_id), state_status, workspace_status,
                               healthy, research_config_ready, target_ready, reason,
                               observation_ready,
                               probe.get("snapshot_status"), probe.get("run_status"),
                               probe.get("snapshot_shape_valid"), probe.get("aria_shape_valid"),
                               probe.get("page_url_present"), probe.get("page_url_canonicalizable"),
                               probe.get("exception_type"), probe.get("exception_message"),
                               probe.get("exception_cause"), observation_probe_attempted,
                               probe.get("navigation_readiness_attempts", 0),
                               probe.get("navigation_readiness_http_status"),
                               bool(probe.get("navigation_readiness_succeeded")),
                               bool(probe.get("post_nav_observation_attempted")),
                               bool(probe.get("post_nav_observation_succeeded")),
                               probe.get("post_nav_page_url"))


def run_live_with_governed_effects(*, space_id: str, research_run: V16SyntheticRun,
                                   create_effect: Callable[[Any], Any],
                                   read_effect: Callable[[Any], Any],
                                   title: str = TITLE, content: str = CONTENT) -> SyntheticHarnessResult:
    """Compose live-run semantics using already-governed effect ports only.

    The harness accepts effect callables supplied by the committed IAOS
    executor composition. It never constructs a Page HTTP request itself.
    """
    return run_synthetic_preflight(
        spaces=({"id": space_id},), research_run=research_run,
        title=title, content=content,
    ) if (create_effect is None or read_effect is None) else _run_with_effects(
        space_id, research_run, create_effect, read_effect, title, content,
    )


def _run_with_effects(space_id: str, research_run: V16SyntheticRun,
                      create_effect: Callable[[Any], Any], read_effect: Callable[[Any], Any],
                      title: str, content: str) -> SyntheticHarnessResult:
    trace = HarnessTrace()
    bound = discover_exactly_one_space(({"id": space_id},), trace)
    trace.emit("RESEARCH_STARTED")
    composition_api = V16OpenDotsArtifactComposition(research_run, bound.space_id,
                                                      create_effect, read_effect)
    if not composition_api.research_complete:
        trace.emit("RESEARCH_GATE_FAIL")
        return SyntheticHarnessResult(False, bound, None, trace,
                                      HarnessCounters(0, 0, 0, (), 0, 0, 0),
                                      "research_incomplete")
    trace.emit("RESEARCH_GATE_PASS", publishers=["NASA", "ESA"])
    result = composition_api.propose_and_verify(title=title, content=content)
    trace.emit("STRUCTURED_VERIFIER_PASS" if result.verification and result.verification.verified
               else "STRUCTURED_VERIFIER_FAIL")
    trace.emit("COMPLETION_PASS" if result.completed else "COMPLETION_FAIL")
    return SyntheticHarnessResult(result.completed, bound, result, trace,
                                  HarnessCounters(0, 0, 2, ("NASA", "ESA"),
                                                  int(result.create_attempted),
                                                  int(result.read_attempted), 0),
                                  None if result.completed else result.failure_reason)


def discover_exactly_one_space(spaces: Sequence[Mapping[str, Any]], trace: HarnessTrace) -> BoundSpace:
    """Bind the only workspace space; never select arbitrarily."""
    trace.emit("OPEN_DOTS_PREFLIGHT", endpoint="GET /api/workspace")
    if len(spaces) != 1:
        trace.emit("OPEN_DOTS_PREFLIGHT_FAIL", space_count=len(spaces))
        raise OpenDotsHarnessPreflightError("space_count_must_equal_one")
    space_id = spaces[0].get("id")
    if not isinstance(space_id, str) or not space_id:
        trace.emit("OPEN_DOTS_PREFLIGHT_FAIL", reason="space_id_invalid")
        raise OpenDotsHarnessPreflightError("space_id_invalid")
    bound = BoundSpace(space_id)
    trace.emit("SPACE_BOUND", space_id=space_id)
    return bound


class _SyntheticOpenDotsEffects:
    def __init__(self, bound: BoundSpace, trace: HarnessTrace, *,
                 create_failure: bool = False, read_failure: bool = False,
                 tampered_observation: ArtifactObservation | None = None) -> None:
        self.bound = bound
        self.trace = trace
        self.create_failure = create_failure
        self.read_failure = read_failure
        self.tampered_observation = tampered_observation
        self.receipt: ArtifactReceipt | None = None
        self.create_count = 0
        self.read_count = 0
        self.edit_count = 0

    def create(self, proposal: Any) -> ArtifactReceipt:
        self.trace.emit("ARTIFACT_CREATE_PROPOSED", action=proposal.action,
                        args=sorted(proposal.args))
        if set(proposal.args) != {"space_id", "title", "content"}:
            raise OpenDotsHarnessPreflightError("model_supplied_create_args_rejected")
        if proposal.args["space_id"] != self.bound.space_id:
            raise OpenDotsHarnessPreflightError("space_id_not_prebound")
        self.trace.emit("ARTIFACT_CREATE_AUTHORIZED", action=proposal.action)
        if self.create_failure:
            raise RuntimeError("synthetic_create_failure")
        self.create_count += 1
        self.receipt = ArtifactReceipt(
            self.bound.space_id, "synthetic-page-1", 1,
            proposal.args["title"], proposal.args["content"],
        )
        self.trace.emit("ARTIFACT_CREATE_EXECUTED", action=proposal.action)
        self.trace.emit("ARTIFACT_RECEIPT", space_id=self.receipt.space_id,
                        page_id=self.receipt.page_id, revision=self.receipt.revision)
        return self.receipt

    def read(self, proposal: Any) -> ArtifactObservation:
        self.trace.emit("ARTIFACT_READ_DERIVED", action=proposal.action,
                        args=sorted(proposal.args))
        self.trace.emit("ARTIFACT_READ_AUTHORIZED", action=proposal.action)
        if self.read_failure:
            raise RuntimeError("synthetic_read_failure")
        self.read_count += 1
        if self.receipt is None:
            raise RuntimeError("read_without_receipt")
        observation = self.tampered_observation or ArtifactObservation(
            self.receipt.space_id, self.receipt.page_id, self.receipt.revision,
            self.receipt.title, self.receipt.content,
        )
        self.trace.emit("ARTIFACT_READ_EXECUTED", action=proposal.action)
        self.trace.emit("ARTIFACT_OBSERVATION", space_id=observation.space_id,
                        page_id=observation.page_id, revision=observation.revision)
        return observation


def run_synthetic_preflight(*, spaces: Sequence[Mapping[str, Any]],
                            research_run: V16SyntheticRun | None = None,
                            title: str = TITLE, content: str = CONTENT,
                            create_failure: bool = False, read_failure: bool = False,
                            tampered_observation: ArtifactObservation | None = None,
                            model_args: Mapping[str, Any] | None = None) -> SyntheticHarnessResult:
    """Execute only the deterministic, no-network V1.6 composition preflight."""
    trace = HarnessTrace()
    try:
        bound = discover_exactly_one_space(spaces, trace)
    except OpenDotsHarnessPreflightError as exc:
        return SyntheticHarnessResult(False, None, None, trace,
                                      HarnessCounters(0, 0, 0, (), 0, 0, 0), str(exc))

    run = research_run or make_synthetic_run()
    trace.emit("RESEARCH_STARTED")
    composition = None
    effects = _SyntheticOpenDotsEffects(bound, trace, create_failure=create_failure,
                                         read_failure=read_failure,
                                         tampered_observation=tampered_observation)
    composition_api = V16OpenDotsArtifactComposition(run, bound.space_id,
                                                      effects.create, effects.read)
    if not composition_api.research_complete:
        trace.emit("RESEARCH_GATE_FAIL")
        counters = HarnessCounters(0, 0, 0, (), 0, 0, 0)
        return SyntheticHarnessResult(False, bound, None, trace, counters,
                                      "research_incomplete")
    trace.emit("RESEARCH_GATE_PASS", publishers=["NASA", "ESA"])

    proposed = {"title": title, "content": content} if model_args is None else dict(model_args)
    if set(proposed) != {"title", "content"}:
        trace.emit("ARTIFACT_CREATE_REJECTED", reason="model_args_out_of_contract")
        counters = HarnessCounters(0, 0, 0, ("NASA", "ESA"), 0, 0, 0)
        return SyntheticHarnessResult(False, bound, None, trace, counters,
                                      "model_args_out_of_contract")
    composition = composition_api.propose_and_verify(**proposed)
    if composition.verification is not None:
        trace.emit("STRUCTURED_VERIFIER_PASS" if composition.verification.verified
                   else "STRUCTURED_VERIFIER_FAIL")
    else:
        trace.emit("STRUCTURED_VERIFIER_FAIL", reason=composition.failure_reason)
    trace.emit("COMPLETION_PASS" if composition.completed else "COMPLETION_FAIL")
    counters = HarnessCounters(0, 0, 2, ("NASA", "ESA"), effects.create_count,
                               effects.read_count, effects.edit_count)
    return SyntheticHarnessResult(composition.completed, bound, composition, trace,
                                  counters, None if composition.completed else composition.failure_reason)


def synthetic_gate_report() -> dict[str, Any]:
    """Return the complete no-effect preflight matrix for focused tests/CI."""
    valid = run_synthetic_preflight(spaces=({"id": "space-a"},))
    invalid_artifact = run_synthetic_preflight(
        spaces=({"id": "space-a"},), content="Artemis I — Nov. 16, 2022",
    )
    invalid_research = run_synthetic_preflight(
        spaces=({"id": "space-a"},), research_run=make_synthetic_run(
            chain_b=make_synthetic_run().chain_b.__class__(
                make_synthetic_run().chain_b.publisher,
                "search-b", "read-b", "https://esa.int/rosetta",
                "https://esa.int/rosetta", "unrelated", None,
            ),
        ),
    )
    no_artifact = run_synthetic_preflight(spaces=({"id": "space-a"},), title="", content="")
    return {"valid": valid, "invalid_artifact": invalid_artifact,
            "invalid_research": invalid_research, "no_artifact": no_artifact,
            "live": False, "wombat_actions": 0}


def run_live_operational(dependencies: LiveDependencies | None = None) -> Any:
    """Run research to an explicit handoff, then use the governed OpenDots route."""
    if dependencies is None:
        raise RuntimeError("live_runner_dependencies_required")
    journal = getattr(dependencies, "journal", None)
    if journal is not None:
        journal.append("RUN_STARTED")
    if isinstance(dependencies, LiveDependencies):
        _establish_research_return_context(dependencies)
        _jit_observation_readiness(dependencies)
    if journal is not None:
        journal.append("LOOP_INVOKED")
    result = dependencies.agent_loop.run(dependencies.high_level_goal)
    if not getattr(result, "research_handoff", False):
        return _live_run_report(dependencies, result)
    adapter_diagnostics = getattr(dependencies, "research_adapter_diagnostics", None)
    if adapter_diagnostics is None:
        adapter_diagnostics = []
    research_run = _research_run_from_result(
        result,
        diagnostics=adapter_diagnostics if getattr(
            dependencies, "research_adapter_diagnostics_enabled", False
        ) else None,
    )
    if research_run is None:
        return _live_run_report(dependencies, result)
    composition = V16OpenDotsArtifactComposition(
        research_run, dependencies.space_id or _resolve_space_id(dependencies.opendots_config),
        _executor_effect(dependencies.governed_create_executor),
        _executor_effect(dependencies.governed_read_executor),
    )
    if not composition.research_complete:
        return _live_run_report(dependencies, composition.propose_and_verify(title=TITLE, content=CONTENT))
    return _live_run_report(dependencies, composition.propose_and_verify(title=TITLE, content=CONTENT))


def _establish_research_return_context(dependencies: LiveDependencies) -> None:
    """Install the runner-owned return anchor without touching the browser."""
    try:
        return_url = canonical_source_url(RESEARCH_RETURN_ANCHOR)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("research_return_anchor_invalid") from exc
    _loop, session, _search = dependencies.research_runtime
    session.set_research_return_context(
        ResearchReturnContext(return_url, "runner_bootstrap")
    )


def _jit_observation_readiness(dependencies: LiveDependencies) -> None:
    """Prove the same live adapter can observe immediately before cognition starts."""
    started = time.monotonic()
    journal = getattr(dependencies, "journal", None)
    if journal is not None:
        journal.append("JIT_STARTED")
    _emit_trace(dependencies.trace_sink, {
        "event_type": "JIT_OBSERVATION_STARTED",
    })
    # Do not wrap or reuse this observation: the adapter's exception and message are the
    # fail-closed diagnostic, and AgentLoop must perform its own first observation.
    try:
        dependencies.openbot_runtime.observe()
    except Exception as exc:
        if journal is not None:
            journal.append("JIT_FAILED", exception_type=type(exc).__name__)
        _emit_trace(dependencies.trace_sink, {
            "event_type": "JIT_OBSERVATION_FAILED",
            "exception_type": type(exc).__name__,
        })
        raise
    completed = time.monotonic()
    if journal is not None:
        journal.append("JIT_PASSED", delta_ms=int((completed - started) * 1000))
    _emit_trace(dependencies.trace_sink, {
        "event_type": "JIT_OBSERVATION_COMPLETED",
        "delta_ms": int((completed - started) * 1000),
    })


def _emit_trace(trace: Any, event: Mapping[str, Any]) -> None:
    emit = getattr(trace, "emit", None)
    if callable(emit):
        emit(event)


def _live_run_report(dependencies: Any, result: Any) -> Any:
    diagnostics = getattr(dependencies, "restore_diagnostics", None)
    adapter_enabled = bool(getattr(dependencies, "research_adapter_diagnostics_enabled", False))
    adapter_diagnostics = tuple(
        dict(item) for item in getattr(dependencies, "research_adapter_diagnostics", ())
    )
    durable_adapter_diagnostic = (
        _durable_research_adapter_diagnostic(adapter_diagnostics)
        if adapter_enabled else None
    )
    report = result if diagnostics is None else (
        LiveRunReportWithResearchAdapterDiagnostics if adapter_enabled else LiveRunReport
    )(
        result=result,
        restore_diagnostics=tuple(dict(item) for item in diagnostics),
        restore_diagnostics_count=len(diagnostics),
        restore_failure_diagnostics_count=sum(
            1 for item in diagnostics if item.get("failure_substage") is not None
        ),
        **({"research_adapter_diagnostics": adapter_diagnostics} if adapter_enabled else {}),
    )
    journal = getattr(dependencies, "journal", None)
    if journal is not None and not journal._finalized:
        inner = getattr(report, "result", report)
        completed = bool(getattr(inner, "completed", False))
        result_metadata = {
            "completed": completed,
            "status": getattr(inner, "status", None),
        }
        if durable_adapter_diagnostic is not None:
            result_metadata["research_adapter_diagnostic"] = durable_adapter_diagnostic
        journal.append(
            "RUN_RESULT_READY",
            **result_metadata,
        )
        journal.append("TERMINAL_STATUS", status="PASS" if completed else "FAIL")
    return report


def _resolve_space_id(config: LiveRuntimeConfig) -> str:
    status, payload = _default_readonly_get(config, "/api/workspace")
    if status != 200 or not isinstance(payload, dict):
        raise OpenDotsHarnessPreflightError("opendots_workspace_not_ready")
    spaces = payload.get("spaces")
    if not isinstance(spaces, list) or len(spaces) != 1:
        raise OpenDotsHarnessPreflightError("space_count_must_equal_one")
    space_id = spaces[0].get("id") if isinstance(spaces[0], dict) else None
    if not isinstance(space_id, str) or not space_id:
        raise OpenDotsHarnessPreflightError("space_id_invalid")
    return space_id


def _executor_effect(executor: Any) -> Callable[[Any], Any]:
    def effect(proposal: Any) -> Any:
        args = tuple((name, str(proposal.args[name]) if name == "expected_revision" else proposal.args[name])
                     for name in executor.arg_names)
        raw = executor.run("opendots:page-api", args, None)
        return SimpleNamespace(ok=True, structured_output=raw.content)
    return effect


def _adapter_rejection(diagnostics: list[dict[str, Any]] | None, *, checkpoint: str,
                       reason: str, observed_type: str, **fields: Any) -> None:
    if diagnostics is None:
        return
    record: dict[str, Any] = {
        "checkpoint": checkpoint,
        "reason": reason,
        "observed_type": observed_type,
        "predicate_result": False,
    }
    record.update(fields)
    diagnostics.append(record)


_DURABLE_ADAPTER_DIAGNOSTIC_FIELDS = (
    "first_failing_checkpoint", "first_failing_reason",
    "adapter_input_runtime_type", "deep_read_facts_runtime_type",
    "deep_read_facts_is_list", "deep_read_facts_count", "item_runtime_types",
    "nasa_match_count", "esa_match_count",
    "nasa_matched_item_runtime_type", "esa_matched_item_runtime_type",
    "nasa_source_url_runtime_type", "esa_source_url_runtime_type",
    "nasa_structured_fact_present", "esa_structured_fact_present",
    "nasa_structured_fact_binding_valid", "esa_structured_fact_binding_valid",
    "nasa_extract_result_present", "esa_extract_result_present",
)


def _durable_research_adapter_diagnostic(
    diagnostics: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Project only approved adapter rejection metadata into the durable journal."""
    rejection = next((item for item in diagnostics
                      if item.get("predicate_result") is False
                      and isinstance(item.get("checkpoint"), str)), None)
    if rejection is None:
        return None
    input_record = next((item for item in diagnostics
                         if item.get("event") == "adapter_input"), {})
    values: dict[str, Any] = {
        "first_failing_checkpoint": rejection.get("checkpoint"),
        "first_failing_reason": rejection.get("reason"),
        "adapter_input_runtime_type": input_record.get("input_runtime_type"),
        "deep_read_facts_runtime_type": input_record.get("deep_read_facts_runtime_type"),
        "deep_read_facts_is_list": input_record.get("deep_read_facts_is_list"),
        "deep_read_facts_count": input_record.get("deep_read_facts_count"),
        "item_runtime_types": input_record.get("item_runtime_types"),
    }
    for publisher, prefix in (("NASA", "nasa"), ("ESA", "esa")):
        record = next((item for item in diagnostics
                       if item.get("publisher") == publisher), None)
        values[f"{prefix}_match_count"] = record.get("match_count") if record else 0
        values[f"{prefix}_matched_item_runtime_type"] = (
            record.get("matched_item_runtime_type") if record else None
        )
        values[f"{prefix}_source_url_runtime_type"] = (
            record.get("source_url_runtime_type") if record else None
        )
        values[f"{prefix}_structured_fact_present"] = (
            record.get("structured_fact_present") if record else None
        )
        values[f"{prefix}_structured_fact_binding_valid"] = (
            record.get("structured_fact_binding_valid") if record else None
        )
        values[f"{prefix}_extract_result_present"] = (
            record.get("extracted_fact_present") if record else None
        )
    return {key: values.get(key) for key in _DURABLE_ADAPTER_DIAGNOSTIC_FIELDS}


def _safe_runtime_type(value: Any) -> str:
    return type(value).__name__


def _research_run_from_result(result: Any, *, diagnostics: list[dict[str, Any]] | None = None
                              ) -> V16SyntheticRun | None:
    facts = getattr(result, "deep_read_facts", None)
    if not isinstance(facts, list):
        _adapter_rejection(
            diagnostics,
            checkpoint="CHECKPOINT_1_DEEP_READ_FACTS_CONTAINER",
            reason="deep_read_facts_not_list",
            observed_type=_safe_runtime_type(facts),
            expected_type="list",
        )
        return None
    if diagnostics is not None:
        diagnostics.append({
            "event": "adapter_input",
            "input_runtime_type": _safe_runtime_type(result),
            "deep_read_facts_runtime_type": _safe_runtime_type(facts),
            "deep_read_facts_is_list": True,
            "deep_read_facts_count": len(facts),
            "item_runtime_types": tuple(_safe_runtime_type(item) for item in facts),
        })
    admitted_facts = getattr(result, "admitted_facts", None)
    if not isinstance(admitted_facts, list):
        _adapter_rejection(diagnostics, checkpoint="CHECKPOINT_1_ADMITTED_FACTS_CONTAINER",
                           reason="admitted_facts_not_list", observed_type=_safe_runtime_type(admitted_facts),
                           expected_type="list")
        return None
    chains: list[PublisherChain] = []
    for publisher in (NASA, ESA):
        matches = [item for item in facts
                   if (urlparse(getattr(item, "source_url", "")).hostname or "").lower().removeprefix("www.")
                   == publisher.registered_domain]
        deep = matches[0] if matches else None
        if deep is None:
            _adapter_rejection(
                diagnostics,
                checkpoint=("CHECKPOINT_2_NASA_PUBLISHER_MATCH" if publisher is NASA
                            else "CHECKPOINT_4_ESA_PUBLISHER_MATCH"),
                reason=f"{publisher.publisher_id.lower()}_source_url_not_matched",
                observed_type=_safe_runtime_type(facts),
                match_count=0,
                expected_host=publisher.registered_domain,
            )
            return None
        structured = next((fact for fact in admitted_facts
                           if isinstance(fact, AdmittedResearchFact)
                           and fact.source_url == deep.source_url), None)
        binding_ok = bool(structured is not None
                          and structured.search_execution_id == deep.search_execution_id
                          and structured.read_execution_id == deep.read_execution_id
                          and structured.evidence_digest == deep.result_digest)
        admitted = (structured.claim_text.strip()
                    if binding_ok and isinstance(structured.claim_text, str) and structured.claim_text.strip()
                    else None)
        if admitted is None:
            _adapter_rejection(
                diagnostics,
                checkpoint=("CHECKPOINT_3_NASA_FACT_ADMISSION" if publisher is NASA
                            else "CHECKPOINT_5_ESA_FACT_ADMISSION"),
                reason=f"{publisher.publisher_id.lower()}_structured_fact_missing_or_unbound",
                publisher=publisher.publisher_id,
                match_count=len(matches),
                matched_item_runtime_type=_safe_runtime_type(deep),
                source_url_runtime_type=_safe_runtime_type(getattr(deep, "source_url", None)),
                observed_type=_safe_runtime_type(getattr(deep, "visible_text_excerpt", None)),
                structured_fact_present=structured is not None,
                structured_fact_binding_valid=binding_ok,
                extracted_fact_present=False,
            )
            return None
        if diagnostics is not None:
            diagnostics.append({
                "event": "adapter_checkpoint_pass",
                "checkpoint": ("CHECKPOINT_3_NASA_FACT_ADMISSION" if publisher is NASA
                                else "CHECKPOINT_5_ESA_FACT_ADMISSION"),
                "publisher": publisher.publisher_id,
                "match_count": len(matches),
                "matched_item_runtime_type": _safe_runtime_type(deep),
                "source_url_runtime_type": _safe_runtime_type(getattr(deep, "source_url", None)),
                "source_host": (urlparse(getattr(deep, "source_url", "")).hostname or "").lower().removeprefix("www."),
                "structured_fact_present": True,
                "structured_fact_binding_valid": True,
                "extracted_fact_present": True,
            })
        chains.append(PublisherChain(
            publisher, deep.search_execution_id, deep.read_execution_id,
            publisher.canonical_document, deep.source_url,
            deep.visible_text_excerpt, admitted,
        ))
    return V16SyntheticRun(chains[0], chains[1], CONTENT)


def dispatch_mode(mode: str, *, live_runner: Callable[[LiveDependencies], Any] | None = None,
                  dependency_factory: Callable[[], LiveDependencies] | None = None,
                  live_preflight_runner: Callable[[], LivePreflightResult] | None = None) -> Any:
    """Dispatch one explicit mode; only ``live-run`` can reach the runner."""
    if mode == "synthetic-preflight":
        return synthetic_gate_report()
    if mode == "live-preflight":
        return (live_preflight_runner or live_preflight)()
    if mode == "live-run":
        dependencies = (dependency_factory or build_live_dependencies)()
        return (live_runner or run_live_operational)(dependencies)
    raise ValueError("mode_invalid")


def _serialize_live_result(result: Any) -> str:
    """Serialize a completed live result without depending on terminal encoding."""
    value = asdict(result) if is_dataclass(result) else result
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=LIVE_MODES, required=True)
    parser.add_argument("--computer-env")
    parser.add_argument("--agnes-env")
    parser.add_argument("--run-journal-path")
    parser.add_argument("--research-adapter-diagnostics", action="store_true")
    args = parser.parse_args()
    if args.computer_env:
        os.environ["COMPUTER_ENV_FILE"] = args.computer_env
    if args.agnes_env:
        os.environ["AGNES_ENV_FILE"] = args.agnes_env
    if args.mode == "synthetic-preflight":
        report = dispatch_mode(args.mode)
        checks = [
            report["valid"].completed,
            not report["invalid_artifact"].completed,
            not report["invalid_research"].completed,
            not report["no_artifact"].completed,
        ]
        print("V1_6_OPENDOTS_LIVE_HARNESS=SYNTHETIC_PREFLIGHT_ONLY")
        print(f"VALID_RESEARCH_VALID_ARTIFACT={'PASS' if checks[0] else 'FAIL'}")
        print(f"INVALID_ARTIFACT={'NOT_COMPLETE' if checks[1] else 'FAIL'}")
        print(f"INVALID_RESEARCH={'NOT_COMPLETE' if checks[2] else 'FAIL'}")
        print(f"NO_ARTIFACT={'NOT_COMPLETE' if checks[3] else 'FAIL'}")
        print("LIVE_RESEARCH=NO")
        print("LIVE_OPENDOTS_EFFECT=NO")
        return 0 if all(checks) else 1
    if args.mode == "live-run":
        if not args.run_journal_path:
            print("DURABLE_JOURNAL_READY=NO")
            print("PRE_RUN_OPERATIONAL_FAILURE=run_journal_path_required")
            print("LOOP_RUN_CALLS=0")
            print("ATTEMPT_CONSUMED=NO")
            return 4
        try:
            journal = DurableRunJournal(args.run_journal_path, run_id=f"v1-6-{time.time_ns()}")
        except Exception as exc:
            print("DURABLE_JOURNAL_READY=NO")
            print(f"PRE_RUN_OPERATIONAL_FAILURE={type(exc).__name__}")
            print("LOOP_RUN_CALLS=0")
            print("ATTEMPT_CONSUMED=NO")
            return 4
        try:
            runtime_lease, runtime_env = prepare_opendots_runtime()
            result = dispatch_mode(
                args.mode,
                dependency_factory=lambda: build_live_dependencies(
                    env=runtime_env, journal=journal,
                    research_adapter_diagnostics=args.research_adapter_diagnostics,
                ),
            )
            try:
                serialized = _serialize_live_result(result)
            except (TypeError, ValueError) as exc:
                print(f"LIVE_RUN_RESULT_SERIALIZATION_FAILURE={type(result).__name__}:{type(exc).__name__}")
                return 1
            print(f"LIVE_RUN_RESULT={serialized}")
        except Exception as exc:
            _print_startup_failure_evidence(exc)
            journal.append(
                "UNHANDLED_EXCEPTION",
                exception_type=type(exc).__name__,
                exception_message=str(exc)[:500],
                stage="live-run",
            )
            if not journal._finalized:
                state = read_durable_run_journal(journal.path)
                status = "PRE_RUN_OPERATIONAL_FAILURE" if not state["loop_invoked"] else "FAIL"
                journal.append("TERMINAL_STATUS", status=status)
            print("LIVE_RUN_OPERATIONAL_FAILURE=" + str(exc))
            return 1
        finally:
            if 'runtime_lease' in locals():
                runtime_lease.close()
            journal.close()
        return 0
    try:
        runtime_lease, runtime_env = prepare_opendots_runtime()
    except Exception as exc:
        print("LIVE_PREFLIGHT=FAIL")
        print("PRE_RUN_OPERATIONAL_FAILURE=" + str(exc))
        _print_startup_failure_evidence(exc)
        print("MODEL_TURNS=0")
        print("SEARCH_ATTEMPTS=0")
        print("DEEP_READ_ATTEMPTS=0")
        print("PAGE_CREATES=0")
        print("PAGE_READS=0")
        return 1
    try:
        computer = _configured_values(
            COMPUTER_KEYS, file_env="COMPUTER_ENV_FILE", values_source=runtime_env
        )
        result = dispatch_mode(
            args.mode,
            live_preflight_runner=lambda: live_preflight(
                env=runtime_env,
                openbot_health=lambda: _openbot_health_check(computer),
                observation_probe=lambda: _openbot_observation_probe(computer),
            ),
        )
    finally:
        runtime_lease.close()
    print(f"LIVE_PREFLIGHT={'PASS' if result.passed else 'FAIL'}")
    print(f"STATE_HTTP_STATUS={result.state_status}")
    print(f"WORKSPACE_HTTP_STATUS={result.workspace_status}")
    print(f"SPACE_BOUND={'YES' if result.bound_space else 'NO'}")
    print(f"OPENBOT_HEALTHY={'YES' if result.openbot_healthy else 'NO'}")
    print(f"OBSERVATION_PROBE_ATTEMPTED={'YES' if getattr(result, 'observation_probe_attempted', False) else 'NO'}")
    print(f"SNAPSHOT_HTTP_STATUS={getattr(result, 'snapshot_status', None)}")
    print(f"RUN_STATUS_HTTP_STATUS={getattr(result, 'run_status', None)}")
    print(f"SNAPSHOT_PAYLOAD_SHAPE_VALID={getattr(result, 'snapshot_shape_valid', None)}")
    print(f"ARIA_SNAPSHOT_SHAPE_VALID={getattr(result, 'aria_shape_valid', None)}")
    created = bool(getattr(result, 'observation_probe_attempted', False) and
                   getattr(result, 'observation_ready', False))
    print(f"COMPUTER_PAGE_OBSERVATION_CREATED={'YES' if created else 'NO'}")
    print(f"OBSERVED_PAGE_URL_PRESENT={getattr(result, 'observed_page_url_present', None)}")
    print(f"OBSERVED_PAGE_URL_CANONICALIZABLE={getattr(result, 'observed_page_url_canonicalizable', None)}")
    print(f"OBSERVATION_READY={'YES' if (getattr(result, 'observation_probe_attempted', False) and getattr(result, 'observation_ready', False)) else 'NO'}")
    print(f"NAVIGATION_READINESS_ATTEMPTS={getattr(result, 'navigation_readiness_attempts', 0)}")
    print(f"NAVIGATION_READINESS_HTTP_STATUS={getattr(result, 'navigation_readiness_http_status', None)}")
    print(f"NAVIGATION_READINESS_SUCCEEDED={'YES' if getattr(result, 'navigation_readiness_succeeded', False) else 'NO'}")
    print(f"POST_NAV_OBSERVATION_ATTEMPTED={'YES' if getattr(result, 'post_nav_observation_attempted', False) else 'NO'}")
    print(f"POST_NAV_OBSERVATION_SUCCEEDED={'YES' if getattr(result, 'post_nav_observation_succeeded', False) else 'NO'}")
    print(f"POST_NAV_PAGE_URL={getattr(result, 'post_nav_page_url', None)}")
    if getattr(result, 'observation_exception_type', None):
        print(f"OBSERVATION_EXCEPTION_TYPE={result.observation_exception_type}")
        print(f"OBSERVATION_EXCEPTION_MESSAGE={result.observation_exception_message}")
        print(f"OBSERVATION_EXCEPTION_CAUSE={result.observation_exception_cause}")
    print(f"TARGET_POLICY_READY={'YES' if result.target_policy_ready else 'NO'}")
    print("MODEL_TURNS=0")
    print("SEARCH_ATTEMPTS=0")
    print("DEEP_READ_ATTEMPTS=0")
    print("PAGE_CREATES=0")
    print("PAGE_READS=0")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
