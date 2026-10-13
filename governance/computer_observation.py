"""Narrow Governance authority for observing the current Computer page.

``browser.observe`` is non-mutating, never navigates, and accepts no URL or
other argument: it observes whatever page/session the Computer is already bound
to. It reuses the existing authorization, envelope, ledger and AEP machinery
unchanged.
"""
from __future__ import annotations

from typing import Any
from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .authorization import issue_authorized_action
from .contract import AuthorizedAction, CanonicalAction, Decision, PolicyDecision, Proposal, RiskLevel
from .evidence import Event, EventKind


OBSERVE_ACTION = "browser.observe"
OBSERVE_RESOURCE = "browser:current-page"


class ComputerObservationAuthorizer:
    """Issue exactly one authority: observe the current bound page, read-only."""

    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate) -> None:
        self._evidence, self._issuer, self._approval = evidence, issuer, approval

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if proposal.action != OBSERVE_ACTION:
            raise ValueError("unknown_computer_observation")
        if proposal.provenance is None or proposal.provenance.source not in {"user", "agent"}:
            raise ValueError("non_authoritative_provenance")
        if dict(proposal.args):
            raise ValueError("observe_takes_no_args")
        return CanonicalAction(proposal.intent_id, OBSERVE_ACTION, OBSERVE_ACTION, OBSERVE_RESOURCE, (), proposal.provenance)

    def authorize(self, proposal: Proposal) -> AuthorizedAction:
        canon = self.canonicalize(proposal)
        policy = PolicyDecision(Decision.ALLOW, "computer_observation_current_page")
        approval = self._approval.decide(canon, RiskLevel.LOW, policy)
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(Event(EventKind.AUTHORIZATION, proposal.intent_id, {
            "action": canon.action, "opcode": canon.opcode, "resource": canon.resource,
            "args": [], "digest": canon.digest(),
            "resource_identity": None, "resource_binding_id": None,
            "policy_decision": policy.decision.value, "risk": RiskLevel.LOW.value,
            "approval_decision": approval.decision.value, "budget_decision": Decision.ALLOW.value,
            "final_decision": Decision.ALLOW.value,
        }))
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)
