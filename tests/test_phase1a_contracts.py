"""IAOS_PHASE_1A_MINIMAL_CONTRACTS_GATE.

Fake-only tests: no connectors, network, memory, Governance or AEP effects.
"""

import ast
import pathlib

import pytest

from cognitive.agent import DEFAULT_ASSISTANT, DailyBriefAgent
from cognitive.contracts import Proposal, Provenance, Workflow
from cognitive.planner import DailyBriefPlanner, run_fake_workflow
from cognitive.provider import AgnesProvider, LLMResponse
from cognitive.registry import fake_daily_brief_registry

ROOT = pathlib.Path(__file__).resolve().parents[1]


def proposal(intent="Prepare meu briefing diário"):
    return Proposal("proposal-1", intent, DEFAULT_ASSISTANT.assistant_id, Provenance("test", "fixture-1"))


def test_provider_abstraction_and_injected_agnes_adapter():
    seen = []
    provider = AgnesProvider(lambda prompt: (seen.append(prompt) or LLMResponse("síntese", "agnes-test", "agnes")))
    out = provider.complete("synthesize fake results")
    assert out == LLMResponse("síntese", "agnes-test", "agnes") and seen == ["synthesize fake results"]


def test_agent_and_default_assistant_have_no_memory_implementation():
    assert DailyBriefAgent.agent_id == "daily-brief-agent"
    assert DEFAULT_ASSISTANT.agent is DailyBriefAgent
    assert DEFAULT_ASSISTANT.memory_scope is None


def test_tool_registry_resolves_only_known_capabilities_and_fails_closed():
    registry = fake_daily_brief_registry()
    assert registry.resolve("email.read").tool_id == "fake-email-read"
    with pytest.raises(LookupError, match="unknown_capability"):
        registry.resolve("shell.exec")


def test_planner_is_deterministic_and_produces_no_authority():
    planner = DailyBriefPlanner()
    first = planner.plan(proposal(), DEFAULT_ASSISTANT)
    second = planner.plan(proposal(), DEFAULT_ASSISTANT)
    assert first == second
    assert first.capability_ids == ("email.read", "calendar.read", "web.search")
    assert first.steps[-1].kind == "synthesis"
    assert not hasattr(first, "authorized_action")
    assert not hasattr(first, "execution_envelope")


def test_fake_daily_brief_workflow_resolves_tools_and_returns_structured_results():
    workflow = DailyBriefPlanner().plan(proposal(), DEFAULT_ASSISTANT)
    out = run_fake_workflow(workflow, fake_daily_brief_registry())
    assert len(out.tool_results) == 3
    assert out.workflow == workflow
    assert all(isinstance(result, dict) for result in out.synthesis_input)


def test_cognitive_package_does_not_import_governance_or_emit_authority_types():
    for path in sorted((ROOT / "cognitive").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imports.append((node.module or "").split(".")[0])
        assert "governance" not in imports and "skilleval" not in imports, path.name
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert not names & {"AuthorizedAction", "ExecutionEnvelope"}, path.name


def test_proposal_and_workflow_are_not_governance_authority_objects():
    assert isinstance(proposal(), Proposal)
    workflow = DailyBriefPlanner().plan(proposal(), DEFAULT_ASSISTANT)
    assert isinstance(workflow, Workflow)
    assert not isinstance(workflow, dict)
