"""Authorizer: Proposal -> Canonicalization -> Policy -> Risk -> Approval ->
Budget -> FinalDecision -> Evidence(AUTHORIZATION) -> AuthorizedAction.

This module is the ONLY place allowed to construct ``AuthorizedAction``.
The AUTHORIZATION append is a precondition of the handoff: if it fails, the
outcome is DENY and no AuthorizedAction exists.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional

from .approval import ApprovalGate
from .auth_registry import AuthorizationRecord
from .budget import Budget
from .canonicalize import Canonicalizer, CanonicalizationError
from .contract import (
    ApprovalDecision,
    AuthorizationOutcome,
    AuthorizedAction,
    BudgetDecision,
    Decision,
    FinalDecision,
    PolicyDecision,
    Proposal,
    RiskLevel,
    most_restrictive,
)
from .evidence import Event, EventKind
from .policy import Policy
from .risk import assess


def issue_authorized_action(issuer: Any, intent_id: str, canon: Any, event_id: str) -> AuthorizedAction:
    """Governance-only construction/issuance primitive for specialized authorizers."""
    action = AuthorizedAction(
        intent_id=intent_id, opcode=canon.opcode, resource=canon.resource,
        args=canon.args, digest=canon.digest(), evidence_event_id=event_id,
        action=canon.action, resource_identity=canon.resource_identity,
        resource_binding_id=canon.resource_binding_id,
    )
    issuer.issue(AuthorizationRecord(
        authorization_id=event_id, intent_id=intent_id, action=canon.action,
        canonical_args=canon.args, resource_binding_id=canon.resource_binding_id,
        opcode=canon.opcode, resource=canon.resource, resource_identity=canon.resource_identity,
    ))
    return action


class Authorizer:
    def __init__(
        self,
        canonicalizer: Canonicalizer,
        policy: Policy,
        approval: ApprovalGate,
        budget: Budget,
        store: Any,
        issuer: Any,
    ) -> None:
        self._canon = canonicalizer
        self._policy = policy
        self._approval = approval
        self._budget = budget
        self._store = store
        self._issuer = issuer  # AuthorizationIssuer: held by Governance only

    def authorize(self, proposal: Proposal) -> AuthorizationOutcome:
        intent_id = getattr(proposal, "intent_id", "<invalid>")
        try:
            canon = self._canon.canonicalize(proposal)
        except CanonicalizationError as exc:
            return self._finish(
                intent_id=intent_id,
                canon=None,
                policy=PolicyDecision(Decision.DENY, "canonicalization_rejected:%s" % exc),
                risk=RiskLevel.HIGH,
                approval=ApprovalDecision(Decision.DENY, "not_evaluated"),
                budget=BudgetDecision(Decision.DENY, "not_evaluated"),
            )
        try:
            policy = self._policy.evaluate(canon)
            risk = assess(canon)
            approval = self._approval.decide(canon, risk, policy)
            budget = self._budget.evaluate(canon)
            outcome = self._finish(intent_id, canon, policy, risk, approval, budget)
        except BaseException:
            self._canon.release(canon.resource_binding_id)
            raise
        if outcome.authorized_action is None:  # not ALLOW: nothing may stay open
            self._canon.release(canon.resource_binding_id)
        return outcome

    def _finish(self, intent_id, canon, policy, risk, approval, budget):
        final_decision = most_restrictive(
            policy.decision, approval.decision, budget.decision
        )
        final = FinalDecision(
            final_decision,
            "all_components_allow" if final_decision is Decision.ALLOW else "restricted",
        )
        base = AuthorizationOutcome(
            intent_id=intent_id,
            resource=canon.resource if canon is not None else None,
            policy_decision=policy,
            risk=risk,
            approval_decision=approval,
            budget_decision=budget,
            final_decision=final,
            evidence_event_id=None,
            authorized_action=None,
        )
        event = Event(EventKind.AUTHORIZATION, intent_id, self._payload(base, canon))
        event_id: Optional[str] = None
        try:
            event_id = self._store.append(event)
        except Exception:
            if final.decision is Decision.ALLOW:
                return self._evidence_failed(base)
        outcome = dataclasses.replace(base, evidence_event_id=event_id)
        if final.decision is not Decision.ALLOW or canon is None:
            return outcome
        action = AuthorizedAction(
            intent_id=intent_id,
            opcode=canon.opcode,
            resource=canon.resource,
            args=canon.args,
            digest=canon.digest(),
            evidence_event_id=event_id,
            action=canon.action,
            resource_identity=canon.resource_identity,
            resource_binding_id=canon.resource_binding_id,
        )
        record = AuthorizationRecord(
            authorization_id=action.authorization_id,
            intent_id=intent_id,
            action=canon.action,
            canonical_args=canon.args,
            resource_binding_id=canon.resource_binding_id,
            opcode=canon.opcode,
            resource=canon.resource,
            resource_identity=canon.resource_identity,
        )
        try:  # evidence first (precondition), then the immutable authorization fact
            self._issuer.issue(record)
        except Exception:
            return dataclasses.replace(
                outcome,
                final_decision=FinalDecision(Decision.DENY, "authorization_issue_failed"),
                authorized_action=None,
            )
        return dataclasses.replace(outcome, authorized_action=action)

    @staticmethod
    def _evidence_failed(base: AuthorizationOutcome) -> AuthorizationOutcome:
        return dataclasses.replace(
            base,
            final_decision=FinalDecision(Decision.DENY, "evidence_append_failed"),
            authorized_action=None,
        )

    @staticmethod
    def _payload(base: AuthorizationOutcome, canon) -> Dict[str, Any]:
        return {
            "policy_decision": base.policy_decision.decision.value,
            "policy_reason": base.policy_decision.reason,
            "risk": base.risk.value,
            "approval_decision": base.approval_decision.decision.value,
            "budget_decision": base.budget_decision.decision.value,
            "final_decision": base.final_decision.decision.value,
            "opcode": canon.opcode if canon is not None else None,
            "resource": canon.resource if canon is not None else None,
            "args": [list(p) for p in canon.args] if canon is not None else None,
            "digest": canon.digest() if canon is not None else None,
            "resource_identity": canon.resource_identity if canon is not None else None,
            "resource_binding_id": canon.resource_binding_id if canon is not None else None,
        }
