"""Closed semantic Capability → Tool resolution for fake Phase 1A tools."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Tuple

from .contracts import Capability, Provenance


class Tool(Protocol):
    tool_id: str
    capability_id: str

    def run(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Produce structured data; a Phase 1A fake has no external effect."""


@dataclass(frozen=True)
class FakeTool:
    tool_id: str
    capability_id: str
    result: Mapping[str, Any]

    def run(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(request, Mapping):
            raise TypeError("tool request must be a mapping")
        return dict(self.result)


class ToolRegistry:
    def __init__(self, capabilities: Tuple[Capability, ...], tools: Tuple[Tool, ...]) -> None:
        self._capabilities = {c.capability_id: c for c in capabilities}
        if len(self._capabilities) != len(capabilities):
            raise ValueError("duplicate:capability")
        self._tools = {t.tool_id: t for t in tools}
        if len(self._tools) != len(tools):
            raise ValueError("duplicate:tool")
        if any(t.capability_id not in self._capabilities for t in tools):
            raise ValueError("tool references unknown capability")
        by_capability = {}
        for tool in tools:
            if tool.capability_id in by_capability:
                raise ValueError("multiple tools for capability")
            by_capability[tool.capability_id] = tool
        self._by_capability = by_capability

    def resolve(self, capability: Capability | str) -> Tool:
        capability_id = capability.capability_id if isinstance(capability, Capability) else capability
        if capability_id not in self._capabilities:
            raise LookupError("unknown_capability:" + str(capability_id))
        try:
            return self._by_capability[capability_id]
        except KeyError as exc:
            raise LookupError("no_tool_for_capability:" + capability_id) from exc

    def capabilities(self) -> Tuple[str, ...]:
        return tuple(self._capabilities)


_PROVENANCE = Provenance("iaos.phase1a", "phase1a-v1")


def fake_daily_brief_registry() -> ToolRegistry:
    capabilities = (
        Capability("email.read", "Read email data for a daily brief.", _PROVENANCE),
        Capability("calendar.read", "Read calendar data for a daily brief.", _PROVENANCE),
        Capability("web.search", "Search web data for a daily brief.", _PROVENANCE),
    )
    tools = (
        FakeTool("fake-email-read", "email.read", {"items": ("Inbox: 2 follow-ups",)}),
        FakeTool("fake-calendar-read", "calendar.read", {"items": ("09:00 team sync",)}),
        FakeTool("fake-web-search", "web.search", {"items": ("Weather: clear",)}),
    )
    return ToolRegistry(capabilities, tools)
