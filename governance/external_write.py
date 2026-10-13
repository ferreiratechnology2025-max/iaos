"""Closed, typed authority for the first external write effect.

This module deliberately contains only ``email.send``.  It is not a generic
connector or communication authority.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .authorization import issue_authorized_action
from .contract import AuthorizedAction, CanonicalAction, Decision, PolicyDecision, Proposal, Provenance, RiskLevel
from .evidence import Event, EventKind

EMAIL_SEND_ACTIONS = frozenset({"email.send"})
EMAIL_SEND_OPCODE = "EMAIL_SEND"
EMAIL_SEND_PROVIDER = "google.gmail.v1"


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _email(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or "\r" in value or "\n" in value:
        raise ValueError(name + "_invalid")
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError(name + "_invalid")
    return value


@dataclass(frozen=True)
class EmailSendDescriptor:
    resource: Mapping[str, Any]
    request: Mapping[str, Any]

    def __post_init__(self) -> None:
        resource = dict(self.resource)
        request = dict(self.request)
        if set(resource) != {"provider_ref", "account_ref", "recipient"}:
            raise ValueError("resource_shape")
        if resource.get("provider_ref") != EMAIL_SEND_PROVIDER:
            raise ValueError("provider_rejected")
        for key in ("account_ref",):
            if not isinstance(resource.get(key), str) or not resource[key]:
                raise ValueError("resource_shape")
        recipient = _email(resource.get("recipient"), "recipient")
        if set(request) != {"to", "subject", "body_text", "daily_brief_run_id"}:
            raise ValueError("request_shape")
        if _email(request.get("to"), "to") != recipient:
            raise ValueError("recipient_mismatch")
        if not isinstance(request["subject"], str) or not request["subject"] or "\r" in request["subject"] or "\n" in request["subject"]:
            raise ValueError("subject_invalid")
        if not isinstance(request["body_text"], str) or not request["body_text"]:
            raise ValueError("body_invalid")
        if not isinstance(request["daily_brief_run_id"], str) or not request["daily_brief_run_id"]:
            raise ValueError("run_id_invalid")
        object.__setattr__(self, "resource", resource)
        object.__setattr__(self, "request", request)


class EmailSendAuthorizer:
    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate,
                 identity_provider: Any) -> None:
        self._evidence, self._issuer, self._approval = evidence, issuer, approval
        self._identity_provider = identity_provider

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if proposal.action not in EMAIL_SEND_ACTIONS:
            raise ValueError("unknown_external_write_action")
        if proposal.provenance is None or proposal.provenance.source not in {"user", "agent"}:
            raise ValueError("non_authoritative_provenance")
        descriptor = EmailSendDescriptor(
            json.loads(str(proposal.args.get("resource", ""))),
            json.loads(str(proposal.args.get("request", ""))),
        )
        identity = self._identity_provider()
        if not identity or descriptor.resource["account_ref"] != identity[0] or descriptor.request["to"] != identity[1]:
            raise ValueError("connected_identity_mismatch")
        return CanonicalAction(
            proposal.intent_id, "email.send", EMAIL_SEND_OPCODE,
            _canon(descriptor.resource), (("request", _canon(descriptor.request)),), proposal.provenance,
        )

    def authorize(self, proposal: Proposal) -> AuthorizedAction:
        canon = self.canonicalize(proposal)
        policy = PolicyDecision(Decision.ALLOW, "typed_email_send")
        approval = self._approval.decide(canon, RiskLevel.HIGH, policy)
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(Event(EventKind.AUTHORIZATION, proposal.intent_id, {
            "action": canon.action, "opcode": canon.opcode, "resource": canon.resource,
            "args": [list(p) for p in canon.args], "digest": canon.digest(),
            "resource_identity": None, "resource_binding_id": None,
            "policy_decision": "ALLOW", "risk": "HIGH", "approval_decision": "ALLOW",
            "budget_decision": "ALLOW", "final_decision": "ALLOW",
        }))
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)
