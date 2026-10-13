"""Typed Governance authority for the experimental read-only Computer V0."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .authorization import issue_authorized_action
from .contract import (
    AuthorizedAction,
    CanonicalAction,
    Decision,
    PolicyDecision,
    Proposal,
    RiskLevel,
)
from .evidence import Event, EventKind


COMPUTER_READ_ACTION = "browser.navigate_and_read"
COMPUTER_READ_OPCODE = COMPUTER_READ_ACTION


class ComputerReadAuthorizer:
    """Issue exactly one authority: navigate to one explicit HTTPS URL and read."""

    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate) -> None:
        self._evidence = evidence
        self._issuer = issuer
        self._approval = approval

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if proposal.action != COMPUTER_READ_ACTION:
            raise ValueError("unknown_computer_action")
        if proposal.provenance is None or proposal.provenance.source not in {"user", "agent"}:
            raise ValueError("non_authoritative_provenance")
        requested_url = proposal.args.get("requested_url")
        _validate_requested_url(requested_url)
        return CanonicalAction(
            proposal.intent_id,
            COMPUTER_READ_ACTION,
            COMPUTER_READ_OPCODE,
            requested_url,
            (),
            proposal.provenance,
        )

    def authorize(self, proposal: Proposal) -> AuthorizedAction:
        canon = self.canonicalize(proposal)
        policy = PolicyDecision(Decision.ALLOW, "computer_read_typed_https_url")
        approval = self._approval.decide(canon, RiskLevel.LOW, policy)
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(
            Event(
                EventKind.AUTHORIZATION,
                proposal.intent_id,
                {
                    "action": canon.action,
                    "opcode": canon.opcode,
                    "resource": canon.resource,
                    "args": [],
                    "digest": canon.digest(),
                    "resource_identity": None,
                    "resource_binding_id": None,
                    "policy_decision": policy.decision.value,
                    "risk": RiskLevel.LOW.value,
                    "approval_decision": approval.decision.value,
                    "budget_decision": Decision.ALLOW.value,
                    "final_decision": Decision.ALLOW.value,
                },
            )
        )
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)


def _validate_requested_url(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("requested_url_invalid")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("https_public_url_required")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("public_network_only")
    try:
        import ipaddress

        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("public_network_only")

