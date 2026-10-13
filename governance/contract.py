"""Frozen Phase 0 contract: data types only, no behaviour that grants authority.

Proposal != AuthorizedAction. A Proposal is untrusted input. An AuthorizedAction
is produced by exactly one place (``authorization.Authorizer``), only after an
AUTHORIZATION event was appended to the evidence store.

Known limit (documented, not solved): Python cannot stop hostile code from
calling ``AuthorizedAction(...)`` directly. ``tests/test_architecture.py``
checks this by AST, which only catches *accidental* violations.
"""
from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional, Tuple


class Decision(enum.Enum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    DENY = "DENY"


_RANK = {Decision.ALLOW: 0, Decision.REVIEW: 1, Decision.DENY: 2}


def most_restrictive(*decisions: Decision) -> Decision:
    """ALLOW only if every component is ALLOW. REVIEW is never ALLOW."""
    if not decisions:
        return Decision.DENY
    return max(decisions, key=lambda d: _RANK[d])


class RiskLevel(enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class ActionSpec:
    opcode: str
    risk: RiskLevel
    resource_arg: str
    extra_args: Tuple[str, ...] = ()
    binds_identity: bool = False  # authorization binds the file's identity, not just its path


# Closed registry. Read-only mapping; entries are frozen dataclasses.
ACTION_REGISTRY: Mapping[str, ActionSpec] = MappingProxyType(
    {
        "fs.read": ActionSpec("FS_READ", RiskLevel.LOW, "path", (), True),
        "fs.read_bytes": ActionSpec("FS_READ_BYTES", RiskLevel.LOW, "path", (), True),
        "fs.write": ActionSpec("FS_WRITE", RiskLevel.MEDIUM, "path", ("content",)),
    }
)


@dataclass(frozen=True)
class Provenance:
    source: str
    intent_id: str


@dataclass(frozen=True)
class Proposal:
    intent_id: str
    action: str
    args: Mapping[str, object]
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", MappingProxyType(dict(self.args)))


def action_digest(intent_id, action, opcode, resource, args, identity=None) -> str:
    """Digest binding the exact authority: intent, action, opcode, resource, args
    and (when present) the identity of the resource."""
    parts = [intent_id, action, opcode, resource, [list(p) for p in args]]
    if identity is not None:
        parts.append(identity)
    blob = json.dumps(parts, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CanonicalAction:
    intent_id: str
    action: str
    opcode: str
    resource: str  # canonical (resolved) path, never the raw one
    args: Tuple[Tuple[str, str], ...]  # non-resource args, sorted
    provenance: Optional[Provenance]
    resource_identity: Optional[str] = None  # identity of the OPEN object, when the action binds one
    resource_binding_id: Optional[str] = None  # runtime handle id; NOT part of the digest

    @property
    def action_class(self) -> str:
        return self.action.split(".", 1)[0]

    def digest(self) -> str:
        return action_digest(
            self.intent_id,
            self.action,
            self.opcode,
            self.resource,
            self.args,
            self.resource_identity,
        )


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str


@dataclass(frozen=True)
class ApprovalDecision:
    decision: Decision
    reason: str


@dataclass(frozen=True)
class BudgetDecision:
    decision: Decision
    reason: str


@dataclass(frozen=True)
class FinalDecision:
    decision: Decision
    reason: str


@dataclass(frozen=True)
class AuthorizedAction:
    intent_id: str
    opcode: str
    resource: str
    args: Tuple[Tuple[str, str], ...]
    digest: str
    evidence_event_id: str
    action: str = ""
    resource_identity: Optional[str] = None
    resource_binding_id: Optional[str] = None  # opaque id into the runtime BindingTable (never a handle)

    @property
    def authorization_id(self) -> str:
        """Id of the AUTHORIZATION evidence event that backs this action."""
        return self.evidence_event_id


@dataclass(frozen=True)
class AuthorizationOutcome:
    intent_id: str
    resource: Optional[str]
    policy_decision: PolicyDecision
    risk: RiskLevel
    approval_decision: ApprovalDecision
    budget_decision: BudgetDecision  # never None
    final_decision: FinalDecision  # distinct from policy_decision
    evidence_event_id: Optional[str]
    authorized_action: Optional[AuthorizedAction]
