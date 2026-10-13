"""AuthorizationCorrespondenceMetric: does the authority that was executed correspond EXACTLY to the
authority that was granted?

Decided only from the supplied evidence (both sides are facts, neither is a claim). It authorizes
nothing, consults no Governance and never reads a ClaimedResult. Precedence (same as the other metric):

    known incompatible scope                   -> UNSUPPORTED
    else missing / malformed required evidence -> INSUFFICIENT_EVIDENCE   (None never matches None)
    else compare the six fields                -> PASS | FAIL

PASS needs exact equality of: authorization_id, intent_id, opcode, args (canonical equality, ordered),
resource_binding_id and authorization_digest. Any divergence is FAIL.

Limit: EVIDENCE_SOURCE_INDEPENDENCE=PENDING. A producer that lies consistently on BOTH sides is not detected.
The digest is compared as an opaque value (digest != signature); it is not recomputed here.
"""
from __future__ import annotations

import json
from typing import Any, List

from ..types import AuthorizationCorrespondenceEvidence, AuthorizedAuthority, ExecutedAuthority, MetricResult, Status

SUPPORTED_SCHEMA_VERSION = 1
SCOPE_LABEL = "authority-correspondence/schema-1"

_TEXT_FIELDS = ("authorization_id", "intent_id", "opcode", "resource_binding_id", "authorization_digest")


def _is_text(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def _is_pairs(v: Any) -> bool:
    if not isinstance(v, tuple):
        return False
    return all(isinstance(p, tuple) and len(p) == 2 and isinstance(p[0], str) and isinstance(p[1], str) for p in v)


def _canonical(args: Any) -> str:
    """Exact canonical form of an args tuple (ordered list of [name, value] string pairs)."""
    return json.dumps([list(p) for p in args], separators=(",", ":"), ensure_ascii=False)


class AuthorizationCorrespondenceMetric:
    name = "AuthorizationCorrespondenceMetric"
    scope_label = SCOPE_LABEL

    def evaluate(self, evidence: Any) -> MetricResult:
        label = self.scope_label
        if not isinstance(evidence, AuthorizationCorrespondenceEvidence):
            return MetricResult(self.name, Status.INSUFFICIENT_EVIDENCE, label,
                                observations=("evidence is not an AuthorizationCorrespondenceEvidence",))
        version = evidence.schema_version
        present = isinstance(version, int) and not isinstance(version, bool)
        if present and version != SUPPORTED_SCHEMA_VERSION:
            return MetricResult(self.name, Status.UNSUPPORTED, label, observations=("scope_mismatch:schema_version",))
        missing: List[str] = [] if present else ["schema_version"]
        a, x = evidence.authorized, evidence.executed
        if not isinstance(a, AuthorizedAuthority):
            missing.append("authorized")
        else:
            missing += [f"authorized.{f}" for f in _TEXT_FIELDS if not _is_text(getattr(a, f))]
            if not _is_pairs(a.authorized_args):
                missing.append("authorized.authorized_args")
        if not isinstance(x, ExecutedAuthority):
            missing.append("executed")
        else:
            missing += [f"executed.{f}" for f in _TEXT_FIELDS if not _is_text(getattr(x, f))]
            if not _is_pairs(x.executed_args):
                missing.append("executed.executed_args")
        if missing:
            return MetricResult(self.name, Status.INSUFFICIENT_EVIDENCE, label,
                                observations=tuple(f"missing:{f}" for f in missing))

        violations: List[str] = []
        if a.authorization_id != x.authorization_id:
            violations.append("authorization_id_mismatch")
        if a.intent_id != x.intent_id:
            violations.append("intent_mismatch")
        if a.opcode != x.opcode:
            violations.append("opcode_mismatch")
        if _canonical(a.authorized_args) != _canonical(x.executed_args):
            violations.append("args_mismatch")
        if a.resource_binding_id != x.resource_binding_id:
            violations.append("binding_mismatch")
        if a.authorization_digest != x.authorization_digest:
            violations.append("digest_mismatch")
        if violations:
            return MetricResult(self.name, Status.FAIL, label, violations=tuple(violations))
        return MetricResult(self.name, Status.PASS, label)
