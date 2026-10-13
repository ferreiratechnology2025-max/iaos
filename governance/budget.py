"""Budget. Phase 0 carries ONLY properties verifiable before EXEC.

No call counters, no external-call caps: those need runtime state that does
not exist before EXEC and would be unverifiable here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

from .contract import BudgetDecision, CanonicalAction, Decision


@dataclass(frozen=True)
class Budget:
    forbidden_action_classes: FrozenSet[str] = frozenset({"shell", "net"})

    def evaluate(self, canon: CanonicalAction) -> BudgetDecision:
        if canon.action_class in self.forbidden_action_classes:
            return BudgetDecision(Decision.DENY, "forbidden_action_class")
        return BudgetDecision(Decision.ALLOW, "budget_ok")
