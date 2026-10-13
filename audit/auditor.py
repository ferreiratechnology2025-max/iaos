"""EvidenceAuditor: compares a ClaimedResult with metrics computed from evidence.

AUDIT_DECISION_INDEPENDENCE: no import of governance/runtime, no call into the system under
audit. CLAIM_DOES_NOT_SELECT_VERIFICATION_POLICY: the metric is chosen by the product's
``expected_gate_id``; ``claim.gate_id`` is only compared against it (mismatch -> the metric does
not run, ``CLAIM_GATE_MISMATCH``, never PASS). There is no fallback metric for an unknown gate.
The claim is otherwise used for (1) subject correlation and (2) ``silent_escape`` after the
evaluation; it is never an input to a metric and never fills evidence (CLAIM_NON_EVIDENTIARY).
EVIDENCE_SOURCE_INDEPENDENCE: NOT provided. An AuditReport is knowledge, not authority.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from claims.types import ClaimedResult, ClaimStatus

from .metrics.authorization_correspondence import AuthorizationCorrespondenceMetric
from .metrics.resource_binding import ResourceBindingMetric
from .types import AuditReport, MetricResult, Status

SUBJECT_FIELDS = ("execution_id", "authorization_id")
DEFAULT_EXPECTED_GATE = "RESOURCE_BINDING"


def default_metrics() -> Dict[str, Any]:
    """gate id -> metric, owned by the product. The expected gate (not the claim) picks which one runs."""
    return {
        "RESOURCE_BINDING": ResourceBindingMetric(),
        "AUTHORIZATION_CORRESPONDENCE": AuthorizationCorrespondenceMetric(),
    }


def aggregate(results: Iterable[MetricResult]) -> Status:
    statuses = [r.status for r in results]
    if not statuses:
        return Status.INSUFFICIENT_EVIDENCE
    for s in (Status.FAIL, Status.INSUFFICIENT_EVIDENCE, Status.UNSUPPORTED):
        if s in statuses:
            return s
    return Status.PASS


class EvidenceAuditor:
    def __init__(self, metrics: Optional[Mapping[str, Any]] = None,
                 expected_gate_id: str = DEFAULT_EXPECTED_GATE) -> None:
        self._metrics = dict(metrics) if metrics is not None else default_metrics()
        self.expected_gate_id = expected_gate_id

    def _check_gate(self, claim: ClaimedResult) -> Optional[MetricResult]:
        """The claim's gate must equal the product's expected gate; otherwise nothing is evaluated."""
        if claim.gate_id != self.expected_gate_id:
            return MetricResult("ClaimGateCorrelation", Status.INSUFFICIENT_EVIDENCE, "n/a",
                                violations=("CLAIM_GATE_MISMATCH",),
                                observations=("claim.gate_id differs from the expected gate",))
        return None

    def _select_metric(self, claim: ClaimedResult) -> Optional[Any]:
        # The claim is deliberately unused (AST-tested): the product decides the policy.
        return self._metrics.get(self.expected_gate_id)

    def _correlate(self, claim: ClaimedResult, evidence: Any) -> Optional[MetricResult]:
        """A present evidence subject that differs from the claim's -> SUBJECT_MISMATCH."""
        for name in SUBJECT_FIELDS:
            seen = getattr(evidence, name, None)
            if seen is not None and seen != getattr(claim, name):
                return MetricResult("SubjectCorrelation", Status.INSUFFICIENT_EVIDENCE, "n/a",
                                    violations=("SUBJECT_MISMATCH",),
                                    observations=(f"subject_field:{name}",))
        return None

    def _evaluate_metric(self, claim: ClaimedResult, metric: Any, evidence: Any) -> MetricResult:
        # The claim is deliberately unused here (AST-tested): the metric sees evidence only.
        return metric.evaluate(evidence)

    def audit(self, claim: ClaimedResult, evidence: Any) -> AuditReport:
        results: List[MetricResult] = []
        missing = ()
        gate_mismatch = self._check_gate(claim)
        if gate_mismatch is not None:  # no metric runs for a claim about another gate
            results = [gate_mismatch]
        else:
            metric = self._select_metric(claim)
            if metric is None:  # no fallback: an expected gate without a metric verifies nothing
                missing = (f"no metric for gate {self.expected_gate_id}",)
            else:
                mismatch = self._correlate(claim, evidence)
                if mismatch is not None:  # evidence is about another subject: not evaluated as support
                    results = [mismatch]
                else:
                    results = [self._evaluate_metric(claim, metric, evidence)]
                    missing = tuple(o[len("missing:"):] for r in results for o in r.observations
                                    if o.startswith("missing:"))
        verified = aggregate(results)
        silent_escape = claim.claimed_status is ClaimStatus.PASS and verified is not Status.PASS
        contradictions = ()
        if silent_escape:
            contradictions = (f"claimed PASS but verified {verified.value}",)
        elif claim.claimed_status is ClaimStatus.FAIL and verified is Status.PASS:
            contradictions = ("claimed FAIL but verified PASS",)
        return AuditReport(claim, verified, silent_escape, tuple(results), missing, contradictions)
