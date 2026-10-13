"""Real V1.6 live entrypoint.

The synthetic composition gate remains in ``agent_wombat_v16_e2e``.  This
module is the only V1.6 entrypoint allowed to assemble real Agnes, Brave and
Computer dependencies.  It is intentionally fail-closed: incomplete
configuration never falls back to a fake provider or to the synthetic gate.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from application.agent_runtime import build_agent_loop
from application.computer_runtime import OpenBotComputerAdapter
from application.deep_read_transport_trace import make_traced_transport
from application.daily_brief import configured_agnes_provider
from application.execution_trace import JsonlExecutionTrace, MemoryExecutionTrace
from application.research_completion import RequiredPublisherCompletionPolicy, registered_domain_resolver
from application.source_facts import ResearchBudget, SearchConfig
from application.v16_independent_publisher import (
    ESA, NASA, REQUIRED_DOCUMENTS, PublisherChain, extract_v16_fact, make_synthetic_run, verify_v16, v16_loop_options,
)
from cognitive.agent_contracts import AgentGoal
from governance.aep_executors import BraveSearchExecutor
from scripts.agent_todomvc_e2e import AGNES_KEYS, COMPUTER_KEYS, read_env
from scripts.agent_wombat_e2e import TARGET, mounted_profile_volume
from scripts.agent_wombat_v16_e2e import ALLOWED_ORIGINS

RESEARCH = ResearchBudget(target_valid_sources=2, max_search_attempts=4, max_deep_read_attempts=4)
SEARCH = SearchConfig((ESA.registered_domain, NASA.registered_domain), result_limit=1,
                      max_searches=RESEARCH.max_search_attempts)
MAX_STEPS = 80
MAX_ADMITTED_SOURCES = 4
PUBLISHER_POLICY = RequiredPublisherCompletionPolicy(
    frozenset((NASA.publisher_id, ESA.publisher_id)),
    registered_domain_resolver({NASA.registered_domain: NASA.publisher_id,
                                ESA.registered_domain: ESA.publisher_id}),
    required_documents=REQUIRED_DOCUMENTS,
)
GOAL = AgentGoal(
    goal_id="independent-publisher-research-v16",
    objective=(
        "Pesquise Artemis I na organização responsável e Rosetta na organização responsável.\n\n"
        "Determine a data de lançamento de cada missão.\n\n"
        "Crie uma página comparativa concisa com o nome e a data de lançamento de cada missão.\n\n"
        "Use informações das duas organizações. Não publique o site."
    ),
    constraints=(), max_steps=MAX_STEPS,
)

TARGET_MARKERS = ("Artemis I", "Nov. 16, 2022", "Rosetta", "2 March 2004")


def _complete(computer: dict[str, str], agnes: dict[str, str], brave: dict[str, str]) -> bool:
    return (len(computer) == len(COMPUTER_KEYS)
            and len(agnes) == len(AGNES_KEYS)
            and "BRAVE_SEARCH_API_KEY" in brave)


def validate_live_configuration(computer: dict[str, str], agnes: dict[str, str], brave: dict[str, str]) -> None:
    """Reject missing live dependencies before constructing cognition."""
    if not _complete(computer, agnes, brave):
        raise RuntimeError("LIVE_CONFIGURATION_INCOMPLETE")


def create_live_trace(trace_path: str | os.PathLike[str], *, run_id: str) -> JsonlExecutionTrace:
    """Create the required live trace before constructing or running cognition."""
    path = Path(trace_path).expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    try:
        path.relative_to(repository)
    except ValueError:
        pass
    else:
        raise RuntimeError("LIVE_TRACE_PATH_INSIDE_REPOSITORY")
    if path.exists():
        raise RuntimeError("LIVE_TRACE_PATH_PREEXISTING")
    if not path.parent.exists():
        raise RuntimeError("LIVE_TRACE_PARENT_MISSING")
    try:
        return JsonlExecutionTrace(path, run_id)
    except (OSError, ValueError) as exc:
        raise RuntimeError("LIVE_TRACE_PREFLIGHT_FAIL") from exc


def _git_state() -> tuple[str, bool]:
    head = subprocess.check_output(("git", "rev-parse", "HEAD"), text=True).strip()
    dirty = bool(subprocess.check_output(("git", "status", "--porcelain"), text=True).strip())
    return head, dirty


def preflight_live_v16(computer: dict[str, str], agnes: dict[str, str], brave: dict[str, str],
                       *, expected_head: str, profile_volume: str, profile_ledger: str,
                       container: str = "openbot-computer-takeover-gate",
                       port_factory=OpenBotComputerAdapter,
                       mounted_volume_fn=mounted_profile_volume,
                       git_state_fn=_git_state,
                       trace_sink=None,
                       wait_seconds: float = 0.0) -> tuple[dict[str, str], Any]:
    """Run the operational gate before constructing cognition or a search executor."""
    head, dirty = git_state_fn()
    report = {
        "HEAD_EXPECTED": expected_head,
        "HEAD": head,
        "WORKING_TREE_RELEVANT": "CLEAN" if not dirty else "DIRTY",
        "PROFILE_UNUSED": "NO",
        "PENDING_TAKEOVER": "NO",
        "TARGET_ARTIFACT_ALREADY_PRESENT": "YES",
        "VALID_SOURCE_COUNT": "0",
        "REQUIRED_PUBLISHERS_SATISFIED": "0/2",
        "SEARCH_ATTEMPTS_USED": "0",
        "DEEP_READ_ATTEMPTS_USED": "0",
        "COMPUTER_RUNTIME_AVAILABLE": "NO",
        "CLEAN_WORLD_GATE": "FAIL",
    }
    if head != expected_head:
        report["PREFLIGHT_FAILURE"] = "HEAD_MISMATCH"
        return report, None
    if dirty:
        report["PREFLIGHT_FAILURE"] = "WORKING_TREE_DIRTY"
        return report, None
    try:
        validate_live_configuration(computer, agnes, brave)
    except RuntimeError:
        report["PREFLIGHT_FAILURE"] = "LIVE_CONFIGURATION_INCOMPLETE"
        return report, None
    used = []
    if os.path.exists(profile_ledger):
        with open(profile_ledger, encoding="utf-8") as fh:
            used = [line.strip() for line in fh if line.strip()]
    try:
        mounted = mounted_volume_fn(container)
    except TypeError:
        # Small injected test doubles may not need the container selector.
        mounted = mounted_volume_fn()
    report["PROFILE_UNUSED"] = "YES" if profile_volume not in used else "NO"
    if report["PROFILE_UNUSED"] != "YES" or mounted != profile_volume:
        report["PREFLIGHT_FAILURE"] = "PROFILE_NOT_NEW_OR_NOT_MOUNTED"
        return report, None
    if trace_sink is not None and isinstance(port_factory, type) and issubclass(port_factory, OpenBotComputerAdapter):
        port = port_factory(computer["IAOS_COMPUTER_URL"], computer["IAOS_COMPUTER_BOT_ID"],
                            computer["IAOS_COMPUTER_TOKEN"], transport=make_traced_transport(trace_sink))
    else:
        port = port_factory(computer["IAOS_COMPUTER_URL"], computer["IAOS_COMPUTER_BOT_ID"],
                            computer["IAOS_COMPUTER_TOKEN"])
    report["COMPUTER_RUNTIME_AVAILABLE"] = "YES"
    control = port.read_control_state(None)
    report["PENDING_TAKEOVER"] = "YES" if control.requested else "NO"
    if control.holder != "bot" or control.requested:
        report["PREFLIGHT_FAILURE"] = "COMPUTER_CONTROL_NOT_CLEAN"
        return report, None
    port.navigate(TARGET)
    if wait_seconds:
        time.sleep(wait_seconds)
    observation = port.observe()
    text = observation.aria_snapshot
    target_present = any(marker in text for marker in TARGET_MARKERS)
    report["TARGET_ARTIFACT_ALREADY_PRESENT"] = "YES" if target_present else "NO"
    if not observation.page_url.rstrip("/").startswith(TARGET.rstrip("/")) or target_present:
        report["PREFLIGHT_FAILURE"] = "TARGET_WORLD_NOT_CLEAN"
        return report, None
    report["CLEAN_WORLD_GATE"] = "PASS"
    return report, port


def _live_verifier_factory(holder: dict[str, Any]) -> Callable[[Any], Any]:
    """Adapt live admitted evidence to the frozen deterministic V1.6 verifier."""
    def verifier(observation):
        loop = holder.get("loop")
        session = holder.get("session")
        if loop is None or session is None:
            raise RuntimeError("LIVE_VERIFIER_BEFORE_LOOP_BOUND")
        by_id = {row["execution_id"]: row for row in session.search_ledger}
        chains = []
        for deep in loop.deep_read_facts:
            source = by_id.get(deep.search_execution_id)
            if source is None:
                continue
            publisher = PUBLISHER_POLICY.publisher_resolver(deep.source_url)
            fixture = NASA if publisher == NASA.publisher_id else ESA if publisher == ESA.publisher_id else None
            if fixture is None:
                continue
            chains.append(PublisherChain(fixture, deep.search_execution_id, deep.read_execution_id,
                                         fixture.canonical_document, deep.source_url,
                                         deep.visible_text_excerpt, extract_v16_fact(deep.visible_text_excerpt)))
        if len(chains) != 2:
            # Preserve the verifier's fail-closed result shape without inventing evidence.
            run = make_synthetic_run(chain_a=chains[0] if chains else None,
                                     chain_b=chains[1] if len(chains) > 1 else None,
                                     artifact_preview="V1_6_PREVIEW")
        else:
            run = make_synthetic_run(chain_a=chains[0], chain_b=chains[1],
                                     artifact_preview=observation.content)
        return verify_v16(run, observation)
    return verifier


def build_live_v16_loop(computer: dict[str, str], agnes: dict[str, str], brave: dict[str, str],
                        *, provider_factory=configured_agnes_provider,
                        computer_factory=OpenBotComputerAdapter,
                        search_factory=BraveSearchExecutor, port=None, trace_sink=None,
                        stop_after_research_gate=False):
    """Construct the real dependency boundary without making a provider call."""
    if trace_sink is None:
        raise RuntimeError("LIVE_TRACE_REQUIRED")
    validate_live_configuration(computer, agnes, brave)
    os.environ.update(agnes)
    os.environ.update(brave)
    provider = provider_factory()
    if port is None:
        if computer_factory is OpenBotComputerAdapter:
            port = computer_factory(computer["IAOS_COMPUTER_URL"], computer["IAOS_COMPUTER_BOT_ID"],
                                    computer["IAOS_COMPUTER_TOKEN"],
                                    transport=make_traced_transport(trace_sink))
        else:
            port = computer_factory(computer["IAOS_COMPUTER_URL"], computer["IAOS_COMPUTER_BOT_ID"],
                                     computer["IAOS_COMPUTER_TOKEN"])
    executor = search_factory()
    holder: dict[str, Any] = {}
    loop, session = build_agent_loop(
        provider, port, _live_verifier_factory(holder), allowed_origins=ALLOWED_ORIGINS,
        search=SEARCH, search_executor=executor, allow_read=True, research=RESEARCH,
        max_searches=RESEARCH.max_search_attempts, max_deep_reads=RESEARCH.max_deep_read_attempts,
        max_admitted_sources=MAX_ADMITTED_SOURCES, research_completion_policy=PUBLISHER_POLICY,
        trace_sink=trace_sink,
         stop_after_research_gate=stop_after_research_gate,
        **v16_loop_options(),
    )
    holder.update(loop=loop, session=session)
    return loop, session, provider, executor, port


def live_boundary_report(computer: dict[str, str], agnes: dict[str, str], brave: dict[str, str], **factories) -> dict[str, str]:
    """Synthetic dependency probe: proves construction reaches the live boundary."""
    loop, session, provider, executor, port = build_live_v16_loop(
        computer, agnes, brave, trace_sink=MemoryExecutionTrace("v1-6-boundary"), **factories)
    return {
        "DEDICATED_LIVE_ENTRYPOINT": "YES",
        "LIVE_ENTRYPOINT_CREATES_AGNES_PROVIDER": "YES",
        "LIVE_ENTRYPOINT_CREATES_BRAVE_EXECUTOR": "YES",
        "LIVE_ENTRYPOINT_CREATES_COMPUTER_CLIENT": "YES",
        "LIVE_ENTRYPOINT_BUILDS_REAL_AGENT_LOOP": "YES",
        "AGNES_CALL_WOULD_OCCUR": "YES",
        "BRAVE_EXECUTOR_AVAILABLE": "YES",
        "COMPUTER_RUNTIME_AVAILABLE": "YES",
        "V1_6_COMPLETION_POLICY_WIRED": "YES" if loop._research_completion_policy is PUBLISHER_POLICY else "NO",
        "V1_6_STORE_CAPACITY_4_WIRED": "YES" if loop._max_admitted_sources == 4 else "NO",
        "V1_6_EXTRACTOR_WIRED": "YES" if loop._deep_extractor is extract_v16_fact else "NO",
        "V1_6_VERIFIER_WIRED": "YES",
        "MAX_STEPS": str(MAX_STEPS),
        "SEARCH_BUDGET": str(RESEARCH.max_search_attempts),
        "DEEP_READ_BUDGET": str(RESEARCH.max_deep_read_attempts),
        "NASA_ESA_REQUIRED": "YES",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--computer-env")
    parser.add_argument("--agnes-env")
    parser.add_argument("--expected-head")
    parser.add_argument("--profile-volume")
    parser.add_argument("--trace-path")
    parser.add_argument("--container", default="openbot-computer-takeover-gate")
    parser.add_argument("--profile-ledger", default=r"D:\iaos-secrets\wombat-profiles-used.txt")
    parser.add_argument("--boundary-only", action="store_true", help="build dependencies but never call Agnes")
    args = parser.parse_args(argv)
    if not args.execute_live:
        print("LIVE_EXECUTION_AUTHORIZATION_REQUIRED")
        return 2
    required = (args.computer_env, args.agnes_env, args.expected_head, args.profile_volume)
    if any(not value for value in required):
        parser.error("--execute-live requires --computer-env, --agnes-env and --expected-head and --profile-volume")
    if not args.trace_path:
        print("LIVE_TRACE_PREFLIGHT_FAIL=LIVE_TRACE_PATH_REQUIRED")
        print("LOOP_RUN=NO")
        print("AGNES_CALLS=0")
        print("BRAVE_CALLS=0")
        return 4
    computer = read_env(args.computer_env, COMPUTER_KEYS)
    agnes = read_env(args.agnes_env, AGNES_KEYS)
    brave = read_env(args.agnes_env, ("BRAVE_SEARCH_API_KEY",))
    if args.boundary_only:
        validate_live_configuration(computer, agnes, brave)
        for key, value in live_boundary_report(computer, agnes, brave).items():
            print(f"{key}={value}")
        print("LIVE_AGENT_RUN=NO")
        return 0
    try:
        trace = create_live_trace(args.trace_path, run_id="v1-6-live")
    except RuntimeError as exc:
        print(f"LIVE_TRACE_PREFLIGHT_FAIL={exc}")
        print("LOOP_RUN=NO")
        print("AGNES_CALLS=0")
        print("BRAVE_CALLS=0")
        return 4
    try:
        report, port = preflight_live_v16(
            computer, agnes, brave, expected_head=args.expected_head, profile_volume=args.profile_volume,
            profile_ledger=args.profile_ledger, container=args.container, trace_sink=trace, wait_seconds=6.0)
        for key, value in report.items():
            print(f"{key}={value}")
        if report["CLEAN_WORLD_GATE"] != "PASS":
            print("LIVE_RUN_ABORTED_BEFORE_AGNES")
            return 3
        with open(args.profile_ledger, "a", encoding="utf-8") as fh:
            fh.write(args.profile_volume + "\n")
        loop, session, provider, executor, port = build_live_v16_loop(
            computer, agnes, brave, port=port, trace_sink=trace)
        result = loop.run(GOAL)
        print(f"LIVE_EXECUTION_BOUNDARY_REACHED=YES AGNES_REAL={'YES' if provider.provider_id == 'agnes' else 'NO'}")
        print(f"RUN_STATUS={result.status} TURNS={result.turns}")
        return 0 if result.status == "VERIFIED" else 1
    finally:
        trace.close()


if __name__ == "__main__":
    sys.exit(main())
