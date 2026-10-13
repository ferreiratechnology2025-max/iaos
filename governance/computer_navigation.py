"""Narrow Governance authority: navigate the Computer to a URL inside a trusted allow-list.

The allow-list is a trusted constraint supplied by the application for one run
(never by the model). The authorizer is instantiated per run with an immutable set
of exact origins. It refuses before any effect when the URL's origin is outside it.
"""
from __future__ import annotations

from typing import Any, Iterable

from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .authorization import issue_authorized_action
from .contract import AuthorizedAction, CanonicalAction, Decision, PolicyDecision, Proposal, RiskLevel
from .evidence import Event, EventKind
from .origin import canonical_allowed_origins, canonical_origin

NAVIGATE_ACTION = "browser.navigate"


class ComputerNavigationAuthorizer:
    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate,
                 allowed_origins: Iterable[str]) -> None:
        self._evidence, self._issuer, self._approval = evidence, issuer, approval
        self._allowed = canonical_allowed_origins(allowed_origins)

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if proposal.action != NAVIGATE_ACTION:
            raise ValueError("unknown_computer_navigation")
        if proposal.provenance is None or proposal.provenance.source not in {"user", "agent"}:
            raise ValueError("non_authoritative_provenance")
        if set(proposal.args) != {"url"}:
            raise ValueError("navigate_args")
        url = proposal.args["url"]
        origin = canonical_origin(url)
        if origin not in self._allowed:
            raise ValueError("origin_not_allowed")
        return CanonicalAction(proposal.intent_id, NAVIGATE_ACTION, NAVIGATE_ACTION, url, (), proposal.provenance)

    def authorize(self, proposal: Proposal) -> AuthorizedAction:
        canon = self.canonicalize(proposal)
        policy = PolicyDecision(Decision.ALLOW, "computer_navigation_origin_allowed")
        approval = self._approval.decide(canon, RiskLevel.LOW, policy)
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(Event(EventKind.AUTHORIZATION, proposal.intent_id, {
            "action": canon.action, "opcode": canon.opcode, "resource": canon.resource, "args": [],
            "digest": canon.digest(), "resource_identity": None, "resource_binding_id": None,
            "policy_decision": policy.decision.value, "risk": RiskLevel.LOW.value,
            "approval_decision": approval.decision.value, "budget_decision": Decision.ALLOW.value,
            "final_decision": Decision.ALLOW.value,
        }))
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)
