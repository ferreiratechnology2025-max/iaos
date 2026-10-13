"""Append-only evidence by events.

``InMemoryEvidenceStore`` is a NON-DURABLE test double. Durable persistence is
a separate gate and is not claimed here.
"""
from __future__ import annotations

import copy
import enum
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping


class EventKind(enum.Enum):
    AUTHORIZATION = "AUTHORIZATION"
    EXECUTION_RESULT = "EXECUTION_RESULT"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    intent_id: str
    payload: Mapping[str, Any]


class InMemoryEvidenceStore:
    """Only ``append`` mutates. Readers return copies."""

    def __init__(self) -> None:
        self._events: List[Dict[str, Any]] = []

    def append(self, event: Event) -> str:
        if not isinstance(event, Event) or not isinstance(event.kind, EventKind):
            raise TypeError("append requires an Event")
        seq = len(self._events)
        prev = self._events[-1]["hash"] if self._events else "0" * 64
        record: Dict[str, Any] = {
            "event_id": "ev-%d" % seq,
            "seq": seq,
            "kind": event.kind.value,
            "intent_id": event.intent_id,
            "prev_hash": prev,
            **copy.deepcopy(dict(event.payload)),
        }
        record["hash"] = hashlib.sha256(
            json.dumps(record, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self._events.append(record)
        return record["event_id"]

    def events(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self._events)

    def verify_chain(self) -> bool:
        prev = "0" * 64
        for rec in self._events:
            body = {k: v for k, v in rec.items() if k != "hash"}
            if body["prev_hash"] != prev:
                return False
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            if digest != rec["hash"]:
                return False
            prev = rec["hash"]
        return True
