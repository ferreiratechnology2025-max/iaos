"""Generic, side-effect-free tool invocation envelope for Gate B1.

Gate B1 proves mediation only. Authorization, consumption and effect receipts
remain the responsibility of the later B2 integration.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Mapping

EffectClass = Literal["OBSERVE", "EFFECT"]
EvidenceMode = Literal["DIGEST_ONLY", "STRUCTURED"]


@dataclass(frozen=True)
class ToolInvocation:
    tool_name: str
    tool_namespace: str
    arguments_digest: str
    resource: str
    actor: str
    context: str
    effect_class: EffectClass
    invocation_id: str = ""
    evidence_mode: EvidenceMode = "DIGEST_ONLY"

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ToolInvocation":
        required = (
            "tool_name", "tool_namespace", "arguments_digest", "resource",
            "actor", "context", "effect_class",
        )
        if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
            raise ValueError("invalid_tool_invocation")
        if payload["effect_class"] not in ("OBSERVE", "EFFECT"):
            raise ValueError("invalid_effect_class")
        if len(payload["arguments_digest"]) != 64:
            raise ValueError("invalid_arguments_digest")
        evidence_mode = payload.get("evidence_mode", "DIGEST_ONLY")
        if evidence_mode not in ("DIGEST_ONLY", "STRUCTURED"):
            raise ValueError("invalid_evidence_mode")
        invocation_id = payload.get("invocation_id") or f"tool-invocation-{uuid.uuid4().hex}"
        if not isinstance(invocation_id, str) or not invocation_id:
            raise ValueError("invalid_invocation_id")
        return cls(*(payload[key] for key in required), invocation_id, evidence_mode)  # type: ignore[arg-type]


def arguments_digest(arguments: Any) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mediate_observation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a generic observation and return a non-authorizing mediation result."""
    invocation = ToolInvocation.from_payload(payload)
    if invocation.effect_class != "OBSERVE":
        return {
            "status": "DENIED",
            "decision": "DENY",
            "reason": "B1_EFFECT_NOT_ENABLED",
            "tool_name": invocation.tool_name,
        }
    return {
        "status": "MEDIATED",
        "decision": "ALLOW",
        "effect_class": "OBSERVE",
        "tool_name": invocation.tool_name,
        "arguments_digest": invocation.arguments_digest,
    }
