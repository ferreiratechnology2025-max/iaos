"""Audit data types. Standard library only; nothing here can authorize or execute.

Evidence enters the auditor as plain immutable/serializable values (evidence.types). The auditor
never receives runtime objects (BindingTable, executor, ledger, registry...).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from claims.types import ClaimedResult  # neutral nominal-claim DTO
from evidence.types import (  # neutral DTOs shared with the exporter
    AuthorizationCorrespondenceEvidence,
    AuthorizedAuthority,
    ExecutedAuthority,
    ResourceBindingEvidence,
)


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNSUPPORTED = "UNSUPPORTED"  # != FAIL, and never PASS
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"  # != FAIL, and never PASS


@dataclass(frozen=True)
class Scope:
    platform: str
    filesystem: str
    action: str

    def label(self) -> str:
        return f"{self.platform}/{self.filesystem}/{self.action}"


@dataclass(frozen=True)
class MetricResult:
    metric: str
    status: Status
    scope: str
    violations: Tuple[str, ...] = ()
    observations: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditReport:
    claimed_result: ClaimedResult
    verified_result: Status
    silent_escape: bool
    metric_results: Tuple[MetricResult, ...]
    missing_evidence: Tuple[str, ...]
    contradictions: Tuple[str, ...]
