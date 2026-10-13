"""Closed, authority-free product cognition contracts for Phase 1A."""

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

SCHEMA_VERSION = 1


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid:{name}")
    return value


def _slug(name: str, value: Any) -> str:
    value = _text(name, value)
    if any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for ch in value):
        raise ValueError(f"invalid:{name}")
    return value


def _texts(name: str, values: Any) -> Tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError(f"invalid:{name}")
    out = tuple(_text(f"{name}[]", value) for value in values)
    if len(set(out)) != len(out):
        raise ValueError(f"duplicate:{name}")
    return out


@dataclass(frozen=True)
class Provenance:
    source: str
    source_revision: str

    def __post_init__(self) -> None:
        _text("provenance.source", self.source)
        _text("provenance.source_revision", self.source_revision)


@dataclass(frozen=True)
class Capability:
    """Semantic operation description; it never executes and grants no authority."""

    capability_id: str
    description: str
    provenance: Provenance
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported:capability.schema_version")
        _slug("capability_id", self.capability_id)
        _text("description", self.description)
        if not isinstance(self.provenance, Provenance):
            raise ValueError("invalid:provenance")


@dataclass(frozen=True)
class Agent:
    """Specialized cognition configuration, not an executor or authority holder."""

    agent_id: str
    name: str
    purpose: str
    capability_ids: Tuple[str, ...]
    provenance: Provenance
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported:agent.schema_version")
        _slug("agent_id", self.agent_id)
        _text("name", self.name)
        _text("purpose", self.purpose)
        object.__setattr__(self, "capability_ids", _texts("capability_ids", self.capability_ids))
        if not isinstance(self.provenance, Provenance):
            raise ValueError("invalid:provenance")


@dataclass(frozen=True)
class Proposal:
    """Untrusted structured intent. It cannot be an AuthorizedAction."""

    proposal_id: str
    intent: str
    assistant_id: str
    provenance: Provenance
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported:proposal.schema_version")
        _slug("proposal_id", self.proposal_id)
        _text("intent", self.intent)
        _slug("assistant_id", self.assistant_id)
        if not isinstance(self.provenance, Provenance):
            raise ValueError("invalid:provenance")


@dataclass(frozen=True)
class WorkflowStep:
    """A planned semantic step; capability ids are not executable authority."""

    step_id: str
    kind: str
    capability_id: str | None = None

    def __post_init__(self) -> None:
        _slug("step_id", self.step_id)
        if self.kind not in {"capability", "synthesis"}:
            raise ValueError("invalid:workflow_step.kind")
        if self.kind == "capability":
            _slug("capability_id", self.capability_id)
        elif self.capability_id is not None:
            raise ValueError("invalid:workflow_step.capability_id")


@dataclass(frozen=True)
class Workflow:
    """A planned composition, still without authority or execution handles."""

    workflow_id: str
    proposal_id: str
    steps: Tuple[WorkflowStep, ...]
    provenance: Provenance
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported:workflow.schema_version")
        _slug("workflow_id", self.workflow_id)
        _slug("proposal_id", self.proposal_id)
        if not isinstance(self.steps, (tuple, list)) or not self.steps:
            raise ValueError("invalid:workflow.steps")
        steps = tuple(self.steps)
        if any(not isinstance(step, WorkflowStep) for step in steps):
            raise ValueError("invalid:workflow.steps")
        if len({step.step_id for step in steps}) != len(steps):
            raise ValueError("duplicate:workflow.steps")
        if steps[-1].kind != "synthesis":
            raise ValueError("invalid:workflow.final_step")
        object.__setattr__(self, "steps", steps)
        if not isinstance(self.provenance, Provenance):
            raise ValueError("invalid:provenance")

    @property
    def capability_ids(self) -> Tuple[str, ...]:
        return tuple(step.capability_id for step in self.steps if step.kind == "capability")  # type: ignore[misc]


@dataclass(frozen=True)
class DailyBriefRequest:
    """Closed, deterministic input for one textual Daily Brief run."""

    timezone: str
    calendar_window: Mapping[str, str]
    email_window: Mapping[str, str]
    email_limit: int
    web_queries: Tuple[str, ...]
    web_result_limit: int
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported:daily_brief_request.schema_version")
        _text("timezone", self.timezone)
        for name, window in (("calendar_window", self.calendar_window), ("email_window", self.email_window)):
            if not isinstance(window, Mapping) or set(window) != {"after", "before"}:
                raise ValueError(f"invalid:{name}")
            for key in ("after", "before"):
                _text(f"{name}.{key}", window[key])
        if not isinstance(self.email_limit, int) or isinstance(self.email_limit, bool) or not 1 <= self.email_limit <= 10:
            raise ValueError("invalid:email_limit")
        queries = _texts("web_queries", self.web_queries)
        if not queries or len(queries) > 3:
            raise ValueError("invalid:web_queries")
        if not isinstance(self.web_result_limit, int) or isinstance(self.web_result_limit, bool) or not 1 <= self.web_result_limit <= 5:
            raise ValueError("invalid:web_result_limit")
        object.__setattr__(self, "web_queries", queries)


@dataclass(frozen=True)
class DailyBriefResult:
    """Product result; source refs are execution refs, never LLM-invented."""

    generated_at: str
    summary: str
    calendar_section: str
    email_section: str
    web_section: str
    source_refs: Tuple[str, ...]
    scout_section: str = "No new Scout findings."
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported:daily_brief_result.schema_version")
        for name in ("generated_at", "summary", "calendar_section", "email_section", "web_section", "scout_section"):
            _text(name, getattr(self, name))
        refs = _texts("source_refs", self.source_refs)
        if not refs:
            raise ValueError("invalid:source_refs")
        object.__setattr__(self, "source_refs", refs)
