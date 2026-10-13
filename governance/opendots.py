"""Narrow, typed OpenDots page capabilities.

This is deliberately not a generic HTTP connector.  The only supported
effects are creating one page and reading one page identified by a receipt.
The OpenDots owner token is runtime transport configuration and never enters a
proposal, envelope, ledger record, or model-visible value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .authorization import issue_authorized_action
from .contract import (
    AuthorizedAction,
    CanonicalAction,
    Decision,
    PolicyDecision,
    Proposal,
    Provenance,
    RiskLevel,
)
from .evidence import Event, EventKind


CREATE_ACTION = "opendots.page.create"
READ_ACTION = "opendots.page.read"
CREATE_OPCODE = CREATE_ACTION
READ_OPCODE = READ_ACTION
RESOURCE = "opendots:page-api"
CREATE_ARGS = ("content", "space_id", "title")
READ_ARGS = ("expected_revision", "page_id", "space_id")


@dataclass(frozen=True)
class ArtifactReceipt:
    space_id: str
    page_id: str
    revision: int
    title: str
    content: str


@dataclass(frozen=True)
class ArtifactObservation:
    space_id: str
    page_id: str
    revision: int
    title: str
    content: str


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or "\x00" in value:
        raise ValueError(f"{name}_invalid")
    return value


def _identity(value: Any, name: str) -> str:
    value = _text(value, name)
    if any(ch in value for ch in ("/", "\\", "\r", "\n")):
        raise ValueError(f"{name}_invalid")
    return value


def _revision(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("expected_revision_invalid")
    return str(value)


def _canon_pairs(values: Mapping[str, str], names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((name, values[name]) for name in names)


class _BaseAuthorizer:
    action = ""
    opcode = ""
    risk = RiskLevel.MEDIUM

    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate) -> None:
        self._evidence, self._issuer, self._approval = evidence, issuer, approval

    def _finish(self, proposal: Proposal, canon: CanonicalAction) -> AuthorizedAction:
        approval = self._approval.decide(canon, self.risk, PolicyDecision(Decision.ALLOW, "typed_opendots_page"))
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(Event(EventKind.AUTHORIZATION, proposal.intent_id, {
            "action": canon.action,
            "opcode": canon.opcode,
            "resource": canon.resource,
            "args": [list(p) for p in canon.args],
            "digest": canon.digest(),
            "resource_identity": None,
            "resource_binding_id": None,
            "policy_decision": "ALLOW",
            "risk": self.risk.value,
            "approval_decision": "ALLOW",
            "budget_decision": "ALLOW",
            "final_decision": "ALLOW",
        }))
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)


class OpenDotsPageCreateAuthorizer(_BaseAuthorizer):
    action = CREATE_ACTION
    opcode = CREATE_OPCODE

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if proposal.action != self.action or proposal.provenance is None or proposal.provenance.source not in {"user", "agent", "governance"}:
            raise ValueError("opendots_create_proposal_invalid")
        if set(proposal.args) != {"space_id", "title", "content"}:
            raise ValueError("opendots_create_args_shape")
        values = {
            "space_id": _identity(proposal.args["space_id"], "space_id"),
            "title": _text(proposal.args["title"], "title"),
            "content": _text(proposal.args["content"], "content", allow_empty=True),
        }
        return CanonicalAction(proposal.intent_id, self.action, self.opcode, RESOURCE,
                               _canon_pairs(values, CREATE_ARGS), proposal.provenance)

    def authorize(self, proposal: Proposal) -> AuthorizedAction:
        return self._finish(proposal, self.canonicalize(proposal))


class OpenDotsPageReadAuthorizer(_BaseAuthorizer):
    action = READ_ACTION
    opcode = READ_OPCODE

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if proposal.action != self.action or proposal.provenance is None or proposal.provenance.source not in {"user", "agent", "governance"}:
            raise ValueError("opendots_read_proposal_invalid")
        if set(proposal.args) != {"space_id", "page_id", "expected_revision"}:
            raise ValueError("opendots_read_args_shape")
        values = {
            "space_id": _identity(proposal.args["space_id"], "space_id"),
            "page_id": _identity(proposal.args["page_id"], "page_id"),
            "expected_revision": _revision(proposal.args["expected_revision"]),
        }
        return CanonicalAction(proposal.intent_id, self.action, self.opcode, RESOURCE,
                               _canon_pairs(values, READ_ARGS), proposal.provenance)

    def authorize(self, proposal: Proposal) -> AuthorizedAction:
        return self._finish(proposal, self.canonicalize(proposal))


def read_proposal_from_receipt(receipt: ArtifactReceipt, intent_id: str) -> Proposal:
    """Create the read proposal from a create receipt; the model supplies nothing."""
    if not isinstance(receipt, ArtifactReceipt):
        raise TypeError("receipt_required")
    return Proposal(intent_id, READ_ACTION, {
        "space_id": receipt.space_id,
        "page_id": receipt.page_id,
        "expected_revision": receipt.revision,
    }, Provenance("governance", intent_id))
