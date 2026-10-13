"""Authorization records and the capabilities around them.

AuthorizationRecord        immutable fact about what Governance authorized.
AuthorizationIssuer        WRITE capability (``issue``). Held by Governance only.
AuthorizationView          READ capability (``get_issued``). Held by the AEP.

The Adapter receives NEITHER. There is no globally reachable mutable registry:
``create_authorization_registry()`` returns the two capabilities and keeps the
store private to them.

Phase 0 decisions: records are immutable; there is no TTL/expiration and no
revocation after issue. Only ISSUED records are ever returned to the AEP.

AUTHORIZATION_AUTHENTICITY_SCOPE = in-process architectural boundary.
CRYPTOGRAPHIC_AUTHORIZATION_AUTHENTICITY = NOT CLAIMED.
MALICIOUS_SAME_PROCESS_ISOLATION = NOT CLAIMED (Python cannot provide it).
"""
from __future__ import annotations

import enum
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .envelope import CARDINALITY_SINGLE_USE, SCHEMA_VERSION, authority_digest


class RecordStatus(enum.Enum):
    ISSUED = "ISSUED"
    PENDING = "PENDING"
    REVOKED = "REVOKED"
    DENIED = "DENIED"


def _freeze_args(args: Any) -> Tuple[Tuple[str, str], ...]:
    return tuple((p[0], p[1]) for p in args)


@dataclass(frozen=True)
class AuthorizationRecord:
    authorization_id: str
    intent_id: str
    action: str
    canonical_args: Tuple[Tuple[str, str], ...]
    resource_binding_id: Optional[str]
    # facts the AEP needs to verify the bound resource; NOT part of the digest
    opcode: str = ""
    resource: Optional[str] = None
    resource_identity: Optional[str] = None
    status: RecordStatus = RecordStatus.ISSUED
    schema_version: int = SCHEMA_VERSION
    execution_cardinality: str = CARDINALITY_SINGLE_USE

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_args", _freeze_args(self.canonical_args))

    def authority_digest(self) -> str:
        return authority_digest(
            self.authorization_id,
            self.intent_id,
            self.action,
            self.canonical_args,
            self.resource_binding_id,
            self.schema_version,
            self.execution_cardinality,
        )


class _AuthorizationStore:
    """Private. Reachable only through an Issuer (write) or a View (read)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, AuthorizationRecord] = {}

    def put(self, record: AuthorizationRecord) -> None:
        with self._lock:
            if record.authorization_id in self._records:
                raise ValueError("authorization_already_issued")  # immutable: never replaced
            self._records[record.authorization_id] = record

    def get(self, authorization_id: str) -> Optional[AuthorizationRecord]:
        with self._lock:
            return self._records.get(authorization_id)


class AuthorizationIssuer:
    """WRITE capability. Exposes only ``issue``."""

    def __init__(self, store: _AuthorizationStore) -> None:
        self._store = store

    def issue(self, record: AuthorizationRecord) -> None:
        if not isinstance(record, AuthorizationRecord):
            raise TypeError("issue requires an AuthorizationRecord")
        self._store.put(record)


class AuthorizationView:
    """READ capability. Exposes only ``get_issued``: a non-ISSUED record is
    indistinguishable from a missing one, so it can never reach EXEC."""

    def __init__(self, store: _AuthorizationStore) -> None:
        self._store = store

    def get_issued(self, authorization_id: str) -> Optional[AuthorizationRecord]:
        record = self._store.get(authorization_id)
        if record is None or record.status is not RecordStatus.ISSUED:
            return None
        return record  # frozen dataclass of immutable values: a stable snapshot


def create_authorization_registry() -> Tuple[AuthorizationIssuer, AuthorizationView]:
    store = _AuthorizationStore()
    issuer = AuthorizationIssuer(store)
    view = AuthorizationView(store)
    return issuer, view
