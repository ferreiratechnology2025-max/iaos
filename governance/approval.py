"""Approval gate. REVIEW is not ALLOW; a grant is bound to intent + digest.

Grants come from the approver side only. A Proposal cannot carry one.
"""
from __future__ import annotations

from typing import Set, Tuple

from .contract import (
    ApprovalDecision,
    CanonicalAction,
    Decision,
    PolicyDecision,
    RiskLevel,
)


class ApprovalGate:
    def __init__(self) -> None:
        self._grants: Set[Tuple[str, str]] = set()

    def grant(self, approver_id: str, intent_id: str, digest: str) -> None:
        if not approver_id:
            raise ValueError("approver_id required")
        self._grants.add((intent_id, digest))

    def decide(
        self, canon: CanonicalAction, risk: RiskLevel, policy: PolicyDecision
    ) -> ApprovalDecision:
        if policy.decision is Decision.DENY:
            return ApprovalDecision(Decision.DENY, "policy_denied")
        if risk is RiskLevel.LOW:
            return ApprovalDecision(Decision.ALLOW, "low_risk")
        if (canon.intent_id, canon.digest()) in self._grants:
            return ApprovalDecision(Decision.ALLOW, "approved")
        return ApprovalDecision(Decision.REVIEW, "approval_required")
