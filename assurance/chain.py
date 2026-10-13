"""Product chain: runtime result -> (ClaimProducer, EvidenceExporter) -> Auditor -> AuditReport.

The two flows are independent: the claim is built from the nominal result only, the evidence
from the observables only; neither producer sees the other nor the auditor. The chain OWNS the
``expected_gate_id`` (and so the verification policy); the claim's ``gate_id`` is only validated
against it.
"""
from __future__ import annotations

from typing import Any, Optional

from audit.auditor import EvidenceAuditor
from audit.types import AuditReport
from claims.producer import (
    GATE_AUTHORIZATION_CORRESPONDENCE,
    GATE_RESOURCE_BINDING,
    claim_authorization_correspondence_from_execution_result,
    claim_from_execution_result,
)
from evidence.exporter import export_authorization_correspondence_evidence, export_resource_binding_evidence


class AssuranceChain:
    def __init__(self, *, expected_gate_id: str = GATE_RESOURCE_BINDING,
                 auditor: Optional[EvidenceAuditor] = None) -> None:
        self.expected_gate_id = expected_gate_id
        if auditor is None:
            auditor = EvidenceAuditor(expected_gate_id=expected_gate_id)
        if auditor.expected_gate_id != expected_gate_id:
            raise ValueError("auditor expected_gate_id differs from the chain's")
        self._auditor = auditor

    def audit_execution(self, result: Any, action: Any, *,
                        volume_path: Optional[str] = None) -> AuditReport:
        claim = claim_from_execution_result(result, action)
        evidence = export_resource_binding_evidence(result, action, volume_path=volume_path)
        return self._auditor.audit(claim, evidence)


def audit_execution(result: Any, action: Any, *, volume_path: Optional[str] = None,
                    auditor: Optional[EvidenceAuditor] = None) -> AuditReport:
    return AssuranceChain(auditor=auditor).audit_execution(result, action, volume_path=volume_path)


class AuthorizationCorrespondenceChain:
    """runtime result + issued authorization record -> (claim, two-sided evidence) -> Auditor -> AuditReport.

    Same independence rules as AssuranceChain: the claim and the evidence are produced separately
    (neither sees the other nor the auditor) and this chain OWNS the expected gate. It authorizes nothing
    and never consults Governance: ``authorization`` is the immutable record, passed in by the caller."""

    def __init__(self, *, auditor: Optional[EvidenceAuditor] = None) -> None:
        self.expected_gate_id = GATE_AUTHORIZATION_CORRESPONDENCE
        if auditor is None:
            auditor = EvidenceAuditor(expected_gate_id=self.expected_gate_id)
        if auditor.expected_gate_id != self.expected_gate_id:
            raise ValueError("auditor expected_gate_id differs from the chain's")
        self._auditor = auditor

    def audit_execution(self, result: Any, authorization: Any) -> AuditReport:
        claim = claim_authorization_correspondence_from_execution_result(result, authorization)
        return self._auditor.audit(claim, export_authorization_correspondence_evidence(result, authorization))
