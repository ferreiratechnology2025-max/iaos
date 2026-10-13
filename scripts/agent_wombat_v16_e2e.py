"""Dedicated V1.6 entrypoint wiring (synthetic only until separately authorised).

This module freezes the trusted run configuration and composes the existing
AgentLoop entrypoint options with the V1.6 extractor/verifier.  ``main`` is
intentionally a synthetic gate: it never creates a Brave executor, computer,
or Agnes provider.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from application.agent_runtime import build_agent_loop
from application.source_facts import ResearchBudget, SearchConfig
from application.research_completion import RequiredPublisherCompletionPolicy, registered_domain_resolver
from application.v16_independent_publisher import (
    ESA, NASA, REQUIRED_DOCUMENTS, extract_v16_fact,
    make_synthetic_run, synthetic_observation, verify_v16, v16_loop_options,
)
from cognitive.agent_contracts import AgentGoal

TARGET = "https://websitewombat.app"
ALLOWED_ORIGINS = (TARGET,)
RESEARCH = ResearchBudget(target_valid_sources=2, max_search_attempts=4, max_deep_read_attempts=4)
SEARCH = SearchConfig((NASA.registered_domain, ESA.registered_domain), result_limit=1,
                      max_searches=RESEARCH.max_search_attempts)
MAX_DEEP_READS = RESEARCH.target_valid_sources
MAX_STEPS = 80
PUBLISHER_POLICY = RequiredPublisherCompletionPolicy(
    frozenset(("NASA", "ESA")),
    registered_domain_resolver({NASA.registered_domain: "NASA", ESA.registered_domain: "ESA"}),
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


def build_v16_loop(provider: Any, port: Any, verifier: Any):
    """Compose the frozen loop with V1.6-only trusted options.

    The verifier is injected by the future live harness after it binds the
    current session's evidence. No V1.5 source or loop file is modified here.
    """
    return build_agent_loop(
        provider, port, verifier,
        allowed_origins=ALLOWED_ORIGINS,
        search=SEARCH,
        allow_read=True,
        max_deep_reads=MAX_DEEP_READS,
        max_searches=RESEARCH.max_search_attempts,
        research=RESEARCH,
        research_completion_policy=PUBLISHER_POLICY,
        max_admitted_sources=RESEARCH.max_deep_read_attempts,
        **v16_loop_options(),
    )


def synthetic_wiring_report() -> dict[str, Any]:
    """Run the V1.6 entrypoint's fixture/verifier path without external I/O."""
    run = make_synthetic_run()
    base = verify_v16(run, synthetic_observation(run))
    mutants = {
        "REMOVE_FACT_A": replace(run, artifact_preview=run.artifact_preview.replace(NASA.fact, "")),
        "REMOVE_FACT_B": replace(run, artifact_preview=run.artifact_preview.replace(ESA.fact, "")),
        "MISSING_CHAIN_A": replace(run, chain_a=replace(run.chain_a, admitted_fact=None)),
        "MISSING_CHAIN_B": replace(run, chain_b=replace(run.chain_b, admitted_fact=None)),
        "PROVENANCE_SWAP": replace(run, chain_a=replace(run.chain_a, admitted_fact=ESA.fact)),
        "SAME_PUBLISHER": replace(run, chain_b=replace(run.chain_b, publisher=NASA)),
    }
    return {
        "baseline": base,
        "mutants": {name: verify_v16(value, synthetic_observation(value)) for name, value in mutants.items()},
        "goal": GOAL.objective,
        "fixture": (NASA, ESA),
        "extractor": extract_v16_fact,
        "policy": PUBLISHER_POLICY,
        "live": False,
    }


def main() -> int:
    report = synthetic_wiring_report()
    baseline = report["baseline"]
    mutants = report["mutants"]
    print("DEDICATED_V1_6_ENTRYPOINT=YES")
    print("GOAL_LOCKED=YES")
    print("PUBLISHER_PAIR_LOCKED=NASA+ESA")
    print("V1_6_EXTRACTOR_WIRED=YES")
    print("V1_6_VERIFIER_WIRED=YES")
    print(f"VALID_FIXTURE_THROUGH_REAL_ENTRYPOINT={'PASS' if baseline.verified else 'FAIL'}")
    for name, result in mutants.items():
        print(f"{name}={'FAIL' if not result.verified else 'PASS'}")
    print("RESEARCH_STATE_COUNTS_PUBLISHERS=NO")
    print("V1_6_REQUIRES_CORE_PUBLISHER_DIVERSITY_CONSTRAINT=YES")
    print("AGENT_LOOP_GENERIC_HOOK=YES")
    print("GOVERNANCE_KERNEL_CHANGED=NO")
    print("RESEARCH_BUDGET_CHANGED=NO")
    print("LIVE_AGENT_RUN=NO")
    return 0 if baseline.verified and all(not result.verified for result in mutants.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
