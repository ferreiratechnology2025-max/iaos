"""Deterministic Daily Brief planning and fake execution for Phase 1A."""

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from .agent import Assistant
from .contracts import Proposal, Provenance, Workflow, WorkflowStep
from .registry import ToolRegistry


@dataclass(frozen=True)
class FakeWorkflowResult:
    workflow: Workflow
    tool_results: Tuple[Mapping[str, Any], ...]
    synthesis_input: Tuple[Mapping[str, Any], ...]


class DailyBriefPlanner:
    """Maps the supported intent to one fixed workflow; it never calls an LLM."""

    INTENT = "prepare meu briefing diário"

    def plan(self, proposal: Proposal, assistant: Assistant) -> Workflow:
        if proposal.assistant_id != assistant.assistant_id:
            raise ValueError("proposal assistant mismatch")
        if proposal.intent.strip().casefold() != self.INTENT:
            raise ValueError("unsupported_daily_brief_intent")
        if tuple(assistant.agent.capability_ids) != ("email.read", "calendar.read", "web.search"):
            raise ValueError("daily_brief_agent_capabilities_changed")
        return Workflow(
            workflow_id="daily-brief-workflow-v1",
            proposal_id=proposal.proposal_id,
            steps=(
                WorkflowStep("read-email", "capability", "email.read"),
                WorkflowStep("read-calendar", "capability", "calendar.read"),
                WorkflowStep("search-web", "capability", "web.search"),
                WorkflowStep("synthesize", "synthesis"),
            ),
            provenance=Provenance("iaos.phase1a", "phase1a-v1"),
        )


def run_fake_workflow(workflow: Workflow, registry: ToolRegistry) -> FakeWorkflowResult:
    results = []
    for step in workflow.steps:
        if step.kind == "synthesis":
            continue
        tool = registry.resolve(step.capability_id or "")
        results.append(tool.run({"workflow_id": workflow.workflow_id, "step_id": step.step_id}))
    return FakeWorkflowResult(workflow, tuple(results), tuple(results))
