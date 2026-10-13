"""Generic tool.invoke authorization/consumption kernel for Gate B2."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .consumption import ConsumeResult, ConsumptionLedger, create_consumption_ledger
from .evidence import Event, EventKind, InMemoryEvidenceStore
from .tool_invocation import ToolInvocation
from .durable_tool_state import SQLiteToolStateStore


@dataclass(frozen=True)
class ToolAuthorization:
    authorization_id: str
    execution_id: str
    invocation: ToolInvocation


class ToolInvocationKernel:
    """Generic B2 kernel; it has no knowledge of page/browser/MCP semantics."""

    def __init__(self, *, ledger: ConsumptionLedger | None = None,
                 evidence: InMemoryEvidenceStore | None = None,
                 durable_store: SQLiteToolStateStore | None = None) -> None:
        if durable_store is not None and (ledger is not None or evidence is not None):
            raise ValueError("durable_store_cannot_mix_memory_backing")
        self.durable_store = durable_store
        self.ledger, self._consume = create_consumption_ledger() if ledger is None else (ledger, None)
        if self._consume is None:
            from .consumption import ConsumptionHandle
            self._consume = ConsumptionHandle(ledger)
        self.evidence = evidence or InMemoryEvidenceStore()
        self._authorizations: dict[str, ToolAuthorization] = {}
        self._sequence = 0

    def authorize_and_consume(self, payload: Mapping[str, Any], *, allow_effects: bool) -> dict[str, Any]:
        invocation = ToolInvocation.from_payload(payload)
        suffix = uuid.uuid4().hex if self.durable_store is not None else str(self._sequence)
        auth_id = f"tool-auth-{suffix}"
        execution_id = f"tool-exec-{suffix}"
        self._sequence += 1
        if invocation.effect_class == "OBSERVE":
            if self.durable_store is not None:
                self.durable_store.authorize_observation(invocation, auth_id, execution_id)
            else:
                self.evidence.append(Event(EventKind.AUTHORIZATION, execution_id, {
                    "action": "tool.invoke", "invocation_id": invocation.invocation_id,
                    "tool_name": invocation.tool_name, "resource": invocation.resource,
                    "arguments_digest": invocation.arguments_digest,
                    "effect_class": invocation.effect_class, "evidence_mode": invocation.evidence_mode,
                    "authorization_id": auth_id,
                }))
                self._authorizations[auth_id] = ToolAuthorization(auth_id, execution_id, invocation)
            return {"status": "MEDIATED", "decision": "ALLOW", "effect_class": "OBSERVE",
                    "tool_name": invocation.tool_name, "arguments_digest": invocation.arguments_digest,
                    "invocation_id": invocation.invocation_id, "authorization_id": auth_id,
                    "execution_id": execution_id, "evidence_mode": invocation.evidence_mode}
        if not allow_effects:
            return {"status": "DENIED", "decision": "DENY", "reason": "B1_EFFECT_NOT_ENABLED",
                    "tool_name": invocation.tool_name}
        if self.durable_store is not None:
            self.durable_store.authorize_and_consume(invocation, auth_id, execution_id)
            return {"status": "AUTHORIZED", "decision": "ALLOW", "authorization_id": auth_id,
                    "execution_id": execution_id, "tool_name": invocation.tool_name,
                    "arguments_digest": invocation.arguments_digest}
        self.evidence.append(Event(EventKind.AUTHORIZATION, execution_id, {
            "action": "tool.invoke", "tool_name": invocation.tool_name,
            "tool_namespace": invocation.tool_namespace, "resource": invocation.resource,
            "arguments_digest": invocation.arguments_digest, "actor": invocation.actor,
            "context": invocation.context, "effect_class": invocation.effect_class,
            "authorization_id": auth_id,
        }))
        consumed = self._consume.consume_once(auth_id, execution_id)
        if consumed is not ConsumeResult.CONSUMED:
            return {"status": "DENIED", "decision": "DENY", "reason": "already_consumed",
                    "tool_name": invocation.tool_name}
        self._authorizations[auth_id] = ToolAuthorization(auth_id, execution_id, invocation)
        return {"status": "AUTHORIZED", "decision": "ALLOW", "authorization_id": auth_id,
                "execution_id": execution_id, "tool_name": invocation.tool_name,
                "arguments_digest": invocation.arguments_digest}

    def record_receipt(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        auth_id = payload.get("authorization_id")
        if self.durable_store is not None:
            authorization = self.durable_store.authorization(auth_id)
            if authorization is None:
                raise ValueError("unknown_authorization")
            expected = ToolInvocation(
                authorization["tool_name"], authorization["tool_namespace"],
                authorization["arguments_digest"], authorization["resource"],
                authorization["actor"], authorization["context"], authorization["effect_class"],
                authorization["invocation_id"], authorization["evidence_mode"],
            )
            execution_id = authorization["execution_id"]
        else:
            authorization = self._authorizations.get(auth_id)
            if authorization is None:
                raise ValueError("unknown_authorization")
            expected = authorization.invocation
            execution_id = authorization.execution_id
        for key, value in (("tool_name", expected.tool_name), ("resource", expected.resource),
                           ("arguments_digest", expected.arguments_digest),
                           ("execution_id", execution_id)):
            if payload.get(key) != value:
                raise ValueError("effect_binding_mismatch")
        identity = payload.get("result_identity")
        if not isinstance(identity, Mapping) or (expected.effect_class == "EFFECT" and not identity):
            raise ValueError("missing_effect_identity")
        resource_id = expected.resource.split("/", 1)[1] if expected.resource.startswith("space/") else None
        observed_space = identity.get("spaceId", identity.get("space_id"))
        if resource_id is not None and observed_space is not None and observed_space != resource_id:
            raise ValueError("effect_resource_mismatch")
        if payload.get("invocation_id", expected.invocation_id) != expected.invocation_id:
            raise ValueError("effect_binding_mismatch")
        evidence_mode = payload.get("evidence_mode", expected.evidence_mode)
        if evidence_mode != expected.evidence_mode:
            raise ValueError("receipt_evidence_mode_mismatch")
        structured_evidence = payload.get("structured_evidence")
        if evidence_mode == "DIGEST_ONLY":
            structured_evidence = None
        result_digest = payload.get("result_digest")
        if not isinstance(result_digest, str):
            result_digest = hashlib.sha256(
                json.dumps(dict(identity), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        if len(result_digest) != 64:
            raise ValueError("invalid_result_digest")
        receipt = {"result_identity": dict(identity), "authorization_id": auth_id,
                   "execution_id": execution_id, "tool_name": expected.tool_name,
                   "resource": expected.resource, "arguments_digest": expected.arguments_digest,
                   "invocation_id": expected.invocation_id, "result_digest": result_digest,
                   "evidence_mode": evidence_mode}
        if self.durable_store is not None:
            durable_receipt = self.durable_store.record_receipt(
                {"authorization_id": auth_id, "execution_id": execution_id}, dict(identity),
                result_digest=result_digest, evidence_mode=evidence_mode,
                structured_evidence=structured_evidence,
            )
            return {"status": "RECEIPTED", **receipt, **durable_receipt}
        self.evidence.append(Event(EventKind.EXECUTION_RESULT, execution_id, {
            "action": "tool.invoke", "tool_name": expected.tool_name,
            "resource": expected.resource, "arguments_digest": expected.arguments_digest,
            "authorization_id": auth_id, "execution_id": execution_id,
            "result_identity": dict(identity),
        }))
        return {"status": "RECEIPTED", **receipt}

    def evidence_events(self) -> list[dict[str, Any]]:
        return self.durable_store.evidence_events() if self.durable_store is not None else self.evidence.events()
