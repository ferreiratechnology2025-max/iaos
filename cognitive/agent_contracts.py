"""Closed contracts for the V0 AgentLoop: goal, decision, strict parser.

The model only ever produces an AgentDecision as strict JSON text. It never
supplies authority fields (page_url, browser_session_id, snapshot_id). There is
no repair step: anything that does not parse exactly is a failed turn.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Collection, Mapping, Tuple, Union

from .provider import LLMProvider

CognitionProvider = LLMProvider

ACT_ACTIONS = ("browser.click", "browser.type", "browser.key", "browser.select_option")
MAX_CLAIM_CHARS = 1000
MAX_TEXT_CHARS = 4096
MAX_KEY_CHARS = 64
MAX_OPTION_CHARS = 256
MAX_QUERY_CHARS = 256


class DecisionParseError(ValueError):
    """The model output is not exactly one valid AgentDecision."""


@dataclass(frozen=True)
class AgentGoal:
    goal_id: str
    objective: str
    constraints: Tuple[str, ...] = ()
    success_criteria: Tuple[str, ...] = ()
    max_steps: int = 20

    def __post_init__(self) -> None:
        if not isinstance(self.goal_id, str) or not self.goal_id:
            raise ValueError("goal_id_required")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective_required")
        if not isinstance(self.max_steps, int) or isinstance(self.max_steps, bool) or not 1 <= self.max_steps <= 100:
            raise ValueError("max_steps_invalid")
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "success_criteria", tuple(self.success_criteria))


@dataclass(frozen=True)
class ActDecision:
    action: str
    args: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FinishDecision:
    claim: str


HUMAN_REASON_CODES = ("authentication_required", "challenge_required")


@dataclass(frozen=True)
class RequestHumanDecision:
    """Ask for a person. Closed reason code only: no free text, no ids from the model."""
    reason_code: str


@dataclass(frozen=True)
class NavigateDecision:
    """Open a URL. The model supplies only the semantic URL, never an authority field."""
    url: str


@dataclass(frozen=True)
class WaitDecision:
    """Do nothing and look again. The model chooses no duration: the application owns the interval."""


@dataclass(frozen=True)
class SearchDecision:
    """Run a governed web search. The model supplies only the query: domains, limit and provider are trusted config."""
    query: str


@dataclass(frozen=True)
class ReadSourceDecision:
    """Read one source that a search already returned. The model names only an ordinal: never a URL, origin or id."""
    source: int


@dataclass(frozen=True)
class ResearchClaimProposal:
    """A semantic claim proposed by cognition for one governed deep read."""
    claim_text: str
    read_execution_id: str


@dataclass(frozen=True)
class ResearchHandoffDecision:
    """Research conclusions handed to the kernel for evidence binding."""
    claims: tuple[ResearchClaimProposal, ...]


MAX_SOURCE_ORDINAL = 99
MAX_URL_CHARS = 2048
AgentDecision = Union[ActDecision, FinishDecision, RequestHumanDecision, NavigateDecision, WaitDecision, SearchDecision,
                      ReadSourceDecision, ResearchHandoffDecision]


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise DecisionParseError("duplicate_key")
        out[key] = value
    return out


def _string(value: object, name: str, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty) or len(value) > limit:
        raise DecisionParseError(f"{name}_invalid")
    return value


def parse_decision(text: object, known_refs: Collection[str] | None = None, allow_search: bool = False,
                   allow_read: bool = False, allow_research_handoff: bool = False) -> AgentDecision:
    """Parse exactly one decision. ``known_refs`` are the refs of the trusted last observation.

    ``kind: search`` exists only when the run configuration enables the search capability (opt-in); otherwise it is an
    unknown kind exactly as before."""
    if not isinstance(text, str) or not text.strip():
        raise DecisionParseError("empty_response")
    try:
        data = json.loads(text.strip(), object_pairs_hook=_no_duplicates)
    except DecisionParseError:
        raise
    except ValueError as exc:
        raise DecisionParseError("invalid_json") from exc
    if not isinstance(data, dict):
        raise DecisionParseError("not_an_object")
    kind = data.get("kind")
    if kind == "finish":
        if set(data) != {"kind", "claim"}:
            raise DecisionParseError("unexpected_fields")
        return FinishDecision(_string(data["claim"], "claim", MAX_CLAIM_CHARS))
    if kind == "research_handoff" and allow_research_handoff:
        if set(data) != {"kind", "claims"} or not isinstance(data["claims"], list) or not data["claims"]:
            raise DecisionParseError("claims_invalid")
        proposals = []
        for item in data["claims"]:
            if not isinstance(item, dict) or set(item) != {"claim_text", "read_execution_id"}:
                raise DecisionParseError("claim_proposal_invalid")
            proposals.append(ResearchClaimProposal(
                _string(item["claim_text"], "claim_text", MAX_CLAIM_CHARS),
                _string(item["read_execution_id"], "read_execution_id", 256),
            ))
        return ResearchHandoffDecision(tuple(proposals))
    if kind == "wait":
        if set(data) != {"kind"}:
            raise DecisionParseError("unexpected_fields")
        return WaitDecision()
    if kind == "navigate":
        if set(data) != {"kind", "url"}:
            raise DecisionParseError("unexpected_fields")
        return NavigateDecision(_string(data["url"], "url", MAX_URL_CHARS))
    if kind == "search" and allow_search:
        if set(data) != {"kind", "query"}:
            raise DecisionParseError("unexpected_fields")
        query = " ".join(_string(data["query"], "query", MAX_QUERY_CHARS).split())
        if not query:
            raise DecisionParseError("query_invalid")
        return SearchDecision(query)
    if kind == "read_source" and allow_read:
        if set(data) != {"kind", "source"}:
            raise DecisionParseError("unexpected_fields")
        source = data["source"]
        if not isinstance(source, int) or isinstance(source, bool) or not 1 <= source <= MAX_SOURCE_ORDINAL:
            raise DecisionParseError("source_invalid")
        return ReadSourceDecision(source)
    if kind == "request_human":
        if set(data) != {"kind", "reason_code"}:
            raise DecisionParseError("unexpected_fields")
        if data["reason_code"] not in HUMAN_REASON_CODES:
            raise DecisionParseError("reason_code_invalid")
        return RequestHumanDecision(data["reason_code"])
    if kind != "act":
        raise DecisionParseError("unknown_kind")
    if set(data) != {"kind", "action", "args"}:
        raise DecisionParseError("unexpected_fields")
    action = data["action"]
    if action not in ACT_ACTIONS:
        raise DecisionParseError("unknown_action")
    args = data["args"]
    if not isinstance(args, dict):
        raise DecisionParseError("args_invalid")
    if action == "browser.click":
        allowed, required = {"ref"}, {"ref"}
    elif action == "browser.type":
        allowed, required = {"ref", "text"}, {"ref", "text"}
    elif action == "browser.select_option":
        allowed, required = {"ref", "option"}, {"ref", "option"}
    else:
        allowed, required = {"key", "ref"}, {"key"}
    if not required <= set(args) or not set(args) <= allowed:
        raise DecisionParseError("args_fields")
    clean: dict[str, str] = {}
    if "ref" in args:
        ref = _string(args["ref"], "ref", 128)
        if known_refs is not None and ref not in known_refs:
            raise DecisionParseError("unknown_ref")
        clean["ref"] = ref
    if "text" in args:
        clean["text"] = _string(args["text"], "text", MAX_TEXT_CHARS, allow_empty=True)
    if "key" in args:
        clean["key"] = _string(args["key"], "key", MAX_KEY_CHARS)
    if "option" in args:
        clean["option"] = _string(args["option"], "option", MAX_OPTION_CHARS)
        if not clean["option"].strip():
            raise DecisionParseError("option_invalid")
    return ActDecision(action, clean)
