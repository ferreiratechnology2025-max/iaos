"""ConsumptionLedger: mutable state about whether an authorization was spent.

Authorization facts (registry) and consumption state (ledger) are separate
domains. The AEP holds a ``ConsumptionHandle`` whose ONLY operation is
``consume_once``: exactly one caller can win AVAILABLE -> CONSUMED, atomically.
Nothing un-consumes: a failure after the transition (handoff or effect) leaves
the authorization CONSUMED. Phase 0 prefers "no duplicate authority" over
automatic retry.

SCOPE: single runtime / single process only (an in-memory CAS under a lock).
CROSS_ADAPTER_REPLAY_GATE, CROSS_PROCESS_REPLAY_GATE, RESTART_SAFE_CONSUMPTION
and DURABLE_CONSUMPTION are all PENDING. Do not read this as exactly-once
execution across processes.
"""
from __future__ import annotations

import enum
import threading
from typing import Dict, Optional, Tuple


class ConsumeResult(enum.Enum):
    CONSUMED = "CONSUMED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"


class ConsumptionLedger:
    """Read-side view for observers/tests lives here; the AEP never gets this."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Dict[str, str] = {}

    def _cas(self, key: str, value: str) -> bool:
        with self._lock:
            if key in self._state:
                return False
            self._state[key] = value
            return True

    def consumed_by(self, authorization_id: str) -> Optional[str]:
        """execution_id that consumed ``authorization_id``, or None if AVAILABLE."""
        with self._lock:
            return self._state.get(authorization_id)

    def consumed_count(self) -> int:
        with self._lock:
            return len(self._state)


class ConsumptionHandle:
    """AEP capability. Exposes only ``consume_once``."""

    def __init__(self, ledger: ConsumptionLedger) -> None:
        self._ledger = ledger

    def consume_once(self, authorization_id: str, execution_id: str) -> ConsumeResult:
        if self._ledger._cas(authorization_id, execution_id):
            return ConsumeResult.CONSUMED
        return ConsumeResult.ALREADY_CONSUMED


def create_consumption_ledger() -> Tuple[ConsumptionLedger, ConsumptionHandle]:
    ledger = ConsumptionLedger()
    handle = ConsumptionHandle(ledger)
    return ledger, handle
