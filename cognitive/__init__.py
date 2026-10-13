"""Minimal product-cognition contracts for Phase 1A.

This package may produce Proposal and Workflow data only. It deliberately has
no Governance, AEP, filesystem, network, memory, or provider side effects.
"""

from .agent import Assistant, DailyBriefAgent
from .contracts import (
    Agent,
    Capability,
    Proposal,
    Provenance,
    Workflow,
    WorkflowStep,
)
from .planner import DailyBriefPlanner, FakeWorkflowResult, run_fake_workflow
from .provider import AgnesProvider, AgnesResponseContractError, AgnesTransportResponse, LLMProvider, LLMResponse
from .registry import Tool, ToolRegistry, fake_daily_brief_registry

__all__ = [
    "Agent", "Assistant", "Capability", "DailyBriefAgent", "DailyBriefPlanner",
    "FakeWorkflowResult", "LLMProvider", "LLMResponse", "AgnesProvider", "AgnesTransportResponse",
    "AgnesResponseContractError", "Proposal",
    "Provenance", "Tool", "ToolRegistry", "Workflow", "WorkflowStep",
    "fake_daily_brief_registry", "run_fake_workflow",
]
