"""ResourceBindingMetric: did an fs.read effect really use the bound resource?

Decided only from the supplied evidence. validated_scope = Windows / NTFS / fs.read;
anything else is UNSUPPORTED (never PASS). Missing or malformed required facts are
INSUFFICIENT_EVIDENCE (never assumed safe).
"""
from __future__ import annotations

from typing import Any, List

from ..types import MetricResult, ResourceBindingEvidence, Scope, Status

VALIDATED_SCOPE = Scope(platform="win32", filesystem="NTFS", action="fs.read")

_SCOPE_FIELDS = ("platform", "filesystem", "action")
_ID_FIELDS = ("execution_id", "authorization_id", "resource_binding_id",
              "authorized_resource_identity", "observed_resource_identity")
_COUNT_FIELDS = ("path_open_calls_during_exec", "bound_handle_reads")


def _is_text(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def _is_count(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


class ResourceBindingMetric:
    name = "ResourceBindingMetric"
    validated_scope = VALIDATED_SCOPE

    def evaluate(self, evidence: Any) -> MetricResult:
        label = self.validated_scope.label()
        if not isinstance(evidence, ResourceBindingEvidence):
            return MetricResult(self.name, Status.INSUFFICIENT_EVIDENCE, label,
                                observations=("evidence is not a ResourceBindingEvidence",))

        # a present scope fact that differs from the validated scope is enough for UNSUPPORTED
        mismatched = [f for f in _SCOPE_FIELDS if _is_text(getattr(evidence, f))
                      and getattr(evidence, f) != getattr(self.validated_scope, f)]
        if mismatched:
            return MetricResult(self.name, Status.UNSUPPORTED, label,
                                observations=tuple(f"scope_mismatch:{f}" for f in mismatched))
        missing: List[str] = [f for f in _SCOPE_FIELDS if not _is_text(getattr(evidence, f))]
        if missing:
            return MetricResult(self.name, Status.INSUFFICIENT_EVIDENCE, label,
                                observations=tuple(f"missing:{f}" for f in missing))
        missing = [f for f in _ID_FIELDS if not _is_text(getattr(evidence, f))]
        missing += [f for f in _COUNT_FIELDS if not _is_count(getattr(evidence, f))]
        if not isinstance(evidence.effect_succeeded, bool):
            missing.append("effect_succeeded")
        if missing:
            return MetricResult(self.name, Status.INSUFFICIENT_EVIDENCE, label,
                                observations=tuple(f"missing:{f}" for f in missing))

        violations: List[str] = []
        if evidence.observed_resource_identity != evidence.authorized_resource_identity:
            violations.append("identity_mismatch")
        if evidence.path_open_calls_during_exec != 0:
            violations.append("path_reopened_during_exec")
        if evidence.bound_handle_reads != 1:
            violations.append("bound_handle_reads_not_one")
        if not evidence.effect_succeeded:
            violations.append("effect_not_successful")
        if violations:
            return MetricResult(self.name, Status.FAIL, label, violations=tuple(violations))
        return MetricResult(self.name, Status.PASS, label)
