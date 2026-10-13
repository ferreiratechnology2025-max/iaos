"""GateClaimProducer: materializes a nominal claim. It does not audit and reads no evidence.

The claimant (the component whose execution is being declared) is the Governed Execution
Runtime; this module only normalizes its nominal outcome into a ClaimedResult. It imports
nothing but ``claims.types`` and receives only the values needed to form the claim: never a
ResourceBindingEvidence, AuditReport, MetricResult, exporter or auditor.

Nominal rule (Phase 0, RESOURCE_BINDING): execution succeeded -> PASS, failed -> FAIL, not
reported -> INCONCLUSIVE. Whether the evidence really supports it is the auditor's job.
"""
from __future__ import annotations

from typing import Any, Union

from .types import SCHEMA_VERSION, ClaimedResult, ClaimProvenance, ClaimStatus

GATE_RESOURCE_BINDING = "RESOURCE_BINDING"
GATE_AUTHORIZATION_CORRESPONDENCE = "AUTHORIZATION_CORRESPONDENCE"
CLAIMANT_RUNTIME = "IAOS_GOVERNED_EXECUTION_RUNTIME"
SOURCE_KIND_EXECUTION_RESULT = "EXECUTION_RESULT"


def produce_claim(*, gate_id: str, nominal_status: Union[ClaimStatus, str], execution_id: str,
                  authorization_id: str, claimant_component: str) -> ClaimedResult:
    """Generic builder (tests / other gates); runtime-facing code uses the gate-specific producers below.

    Preserves the nominal status exactly; derives nothing else."""
    return ClaimedResult(
        schema_version=SCHEMA_VERSION,
        claim_id=f"{gate_id}:{execution_id}",
        gate_id=gate_id,
        claimed_status=ClaimStatus(nominal_status),
        execution_id=execution_id,
        authorization_id=authorization_id,
        claimant_component=claimant_component,
        provenance=ClaimProvenance(SOURCE_KIND_EXECUTION_RESULT, execution_id),
    )


def produce_resource_binding_claim(*, nominal_status: Union[ClaimStatus, str], execution_id: str,
                                   authorization_id: str) -> ClaimedResult:
    """The runtime supplies status and subject only; the gate id is fixed, never chosen by the caller."""
    return produce_claim(gate_id=GATE_RESOURCE_BINDING, nominal_status=nominal_status,
                         execution_id=execution_id, authorization_id=authorization_id,
                         claimant_component=CLAIMANT_RUNTIME)

def claim_from_execution_result(result: Any, action: Any) -> ClaimedResult:
    """RESOURCE_BINDING claim from a runtime result (``ok``, ``execution_id``) and its action."""
    ok = getattr(result, "ok", None)
    if ok is True:
        nominal = ClaimStatus.PASS
    elif ok is False:
        nominal = ClaimStatus.FAIL
    else:
        nominal = ClaimStatus.INCONCLUSIVE
    return produce_resource_binding_claim(
        nominal_status=nominal,
        execution_id=getattr(result, "execution_id", None),
        authorization_id=getattr(action, "authorization_id", None),
    )


def produce_authorization_correspondence_claim(*, nominal_status: Union[ClaimStatus, str], execution_id: str,
                                               authorization_id: str) -> ClaimedResult:
    """Fixed gate id (AUTHORIZATION_CORRESPONDENCE); the runtime supplies only status and subject."""
    return produce_claim(gate_id=GATE_AUTHORIZATION_CORRESPONDENCE, nominal_status=nominal_status,
                         execution_id=execution_id, authorization_id=authorization_id,
                         claimant_component=CLAIMANT_RUNTIME)


def claim_authorization_correspondence_from_execution_result(result: Any, authorization: Any) -> ClaimedResult:
    """AUTHORIZATION_CORRESPONDENCE claim from the nominal outcome (``ok``, ``execution_id``) and the
    authorization's id. The nominal rule is the same as for the other gate; it reads no evidence."""
    ok = getattr(result, "ok", None)
    if ok is True:
        nominal = ClaimStatus.PASS
    elif ok is False:
        nominal = ClaimStatus.FAIL
    else:
        nominal = ClaimStatus.INCONCLUSIVE
    return produce_authorization_correspondence_claim(
        nominal_status=nominal,
        execution_id=getattr(result, "execution_id", None),
        authorization_id=getattr(authorization, "authorization_id", None),
    )
