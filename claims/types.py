"""ClaimedResult: a nominal declaration by a component about a gate. Standard library only.

CLAIM_NON_EVIDENTIARY: a claim is never evidence; no field that exists only here may satisfy a
metric requirement. CLAIM_NON_AUTHORITY: a claim never becomes an authorization, envelope,
consumption or effect. Provenance says only "this declaration says it came from this origin": it
does NOT mean trusted, verified, true or authorized. No signature / MAC in Phase 0.

The schema is closed and the DTO immutable. It deliberately carries no resource identity, no
counters, no binding id, no platform/filesystem and no metric or audit result.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Mapping

SCHEMA_VERSION = 1


class ClaimStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"  # UNSUPPORTED / INSUFFICIENT_EVIDENCE belong to the auditor


def _text(name: str, value: Any) -> None:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class ClaimProvenance:
    source_kind: str
    source_id: str

    def __post_init__(self) -> None:
        _text("provenance.source_kind", self.source_kind)
        _text("provenance.source_id", self.source_id)


@dataclass(frozen=True)
class ClaimedResult:
    schema_version: int
    claim_id: str
    gate_id: str
    claimed_status: ClaimStatus
    execution_id: str
    authorization_id: str
    claimant_component: str
    provenance: ClaimProvenance

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported claim schema_version")
        if not isinstance(self.claimed_status, ClaimStatus):
            raise ValueError("claimed_status must be a ClaimStatus")
        if not isinstance(self.provenance, ClaimProvenance):
            raise ValueError("provenance must be a ClaimProvenance")
        for name in ("claim_id", "gate_id", "execution_id", "authorization_id", "claimant_component"):
            _text(name, getattr(self, name))

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "claim_id": self.claim_id,
            "gate_id": self.gate_id,
            "claimed_status": self.claimed_status.value,
            "execution_id": self.execution_id,
            "authorization_id": self.authorization_id,
            "claimant_component": self.claimant_component,
            "provenance": {"source_kind": self.provenance.source_kind,
                           "source_id": self.provenance.source_id},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClaimedResult":
        names = {f.name for f in fields(cls)}
        if set(data) != names:
            raise ValueError(f"closed schema: expected {sorted(names)}, got {sorted(data)}")
        prov = data["provenance"]
        if not isinstance(prov, Mapping) or set(prov) != {"source_kind", "source_id"}:
            raise ValueError("closed schema: bad provenance")
        return cls(
            schema_version=data["schema_version"],
            claim_id=data["claim_id"],
            gate_id=data["gate_id"],
            claimed_status=ClaimStatus(data["claimed_status"]),
            execution_id=data["execution_id"],
            authorization_id=data["authorization_id"],
            claimant_component=data["claimant_component"],
            provenance=ClaimProvenance(prov["source_kind"], prov["source_id"]),
        )
