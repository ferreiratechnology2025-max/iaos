"""Narrow Governance authority: ask for a person at the Computer. Never takes control.

``browser.request_human`` is an explicit, auditable control-plane request. The
model supplies only a closed ``reason_code``; the application supplies the
``tool_call_id`` (idempotency key). Free text never crosses this boundary.
"""
from __future__ import annotations

from typing import Any

from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .authorization import issue_authorized_action
from .contract import AuthorizedAction, CanonicalAction, Decision, PolicyDecision, Proposal, RiskLevel
from .evidence import Event, EventKind

REQUEST_HUMAN_ACTION = "browser.request_human"
REQUEST_HUMAN_RESOURCE = "browser:human-handoff"
REASON_CODES = ("authentication_required", "challenge_required")


class ComputerHumanRequestAuthorizer:
    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate) -> None:
        self._evidence, self._issuer, self._approval = evidence, issuer, approval

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if proposal.action != REQUEST_HUMAN_ACTION:
            raise ValueError("unknown_computer_human_request")
        if proposal.provenance is None or proposal.provenance.source not in {"user", "agent"}:
            raise ValueError("non_authoritative_provenance")
        if set(proposal.args) != {"reason_code", "tool_call_id"}:
            raise ValueError("request_human_args")
        code, call_id = proposal.args["reason_code"], proposal.args["tool_call_id"]
        if code not in REASON_CODES:
            raise ValueError("reason_code_invalid")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 200:
            raise ValueError("tool_call_id_invalid")
        args = tuple(sorted((("reason_code", code), ("tool_call_id", call_id))))
        return CanonicalAction(proposal.intent_id, REQUEST_HUMAN_ACTION, REQUEST_HUMAN_ACTION,
                               REQUEST_HUMAN_RESOURCE, args, proposal.provenance)

    def authorize(self, proposal: Proposal) -> AuthorizedAction:
        canon = self.canonicalize(proposal)
        policy = PolicyDecision(Decision.ALLOW, "computer_request_human_closed_reason")
        approval = self._approval.decide(canon, RiskLevel.LOW, policy)
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(Event(EventKind.AUTHORIZATION, proposal.intent_id, {
            "action": canon.action, "opcode": canon.opcode, "resource": canon.resource,
            "args": [list(pair) for pair in canon.args], "digest": canon.digest(),
            "resource_identity": None, "resource_binding_id": None,
            "policy_decision": policy.decision.value, "risk": RiskLevel.LOW.value,
            "approval_decision": approval.decision.value, "budget_decision": Decision.ALLOW.value,
            "final_decision": Decision.ALLOW.value,
        }))
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)
