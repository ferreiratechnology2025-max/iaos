"""Agent and Assistant composition for the first product workflow."""

from dataclasses import dataclass

from .contracts import Agent, Provenance


@dataclass(frozen=True)
class Assistant:
    """One composed Assistant; memory_scope is intentionally empty in Phase 1A."""

    assistant_id: str
    name: str
    agent: Agent
    memory_scope: str | None = None
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        if not self.assistant_id or not self.name:
            raise ValueError("invalid:assistant")
        if not isinstance(self.agent, Agent):
            raise ValueError("invalid:assistant.agent")
        if self.memory_scope not in (None, ""):
            raise ValueError("memory_implementation_not_in_phase_1a")
        if self.provenance is not None and not isinstance(self.provenance, Provenance):
            raise ValueError("invalid:assistant.provenance")


DAILY_BRIEF_AGENT = Agent(
    agent_id="daily-brief-agent",
    name="Daily Brief Agent",
    purpose="Compose a deterministic daily briefing workflow from read-only semantic capabilities.",
    capability_ids=("email.read", "calendar.read", "web.search"),
    provenance=Provenance("iaos.phase1a", "phase1a-v1"),
)

DailyBriefAgent = DAILY_BRIEF_AGENT


DEFAULT_ASSISTANT = Assistant(
    assistant_id="default-assistant",
    name="IAOS Personal Assistant",
    agent=DAILY_BRIEF_AGENT,
    memory_scope=None,
    provenance=Provenance("iaos.phase1a", "phase1a-v1"),
)
