"""Narrow Phase 1B authorities for deterministic, fake-only external reads.

These are three distinct authorities.  This module deliberately does not
provide a generic network/connector authority and never performs I/O.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .contract import (
    AuthorizedAction,
    CanonicalAction,
    Decision,
    PolicyDecision,
    Provenance,
    RiskLevel,
)
from .evidence import Event, EventKind
from .authorization import issue_authorized_action


EXTERNAL_READ_ACTIONS = frozenset({"email.read", "calendar.read", "web.search"})
RESULT_AUTHORITY = "DATA_ONLY"
RESULT_PROVENANCE = "TOOL_RESULT"


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _obj(value: str, name: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid:{name}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid:{name}")
    return parsed


@dataclass(frozen=True)
class ExternalReadDescriptor:
    action: str
    resource: Mapping[str, Any]
    args: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.action not in EXTERNAL_READ_ACTIONS:
            raise ValueError("unknown_external_action")
        object.__setattr__(self, "resource", dict(self.resource))
        object.__setattr__(self, "args", dict(self.args))
        _validate_descriptor(self.action, self.resource, self.args)


def _required(resource: Mapping[str, Any], names: Tuple[str, ...]) -> None:
    if set(resource) != set(names) or any(not isinstance(resource[n], str) or not resource[n] for n in names):
        raise ValueError("resource_shape")


def _validate_descriptor(action: str, resource: Mapping[str, Any], args: Mapping[str, Any]) -> None:
    if action == "email.read":
        _required(resource, ("provider_ref", "account_ref", "mailbox_ref"))
    elif action == "calendar.read":
        _required(resource, ("provider_ref", "account_ref", "calendar_ref"))
    else:
        if set(resource) != {"provider_ref", "allowed_domains"} or not isinstance(resource.get("provider_ref"), str):
            raise ValueError("resource_shape")
        domains = resource.get("allowed_domains")
        if not isinstance(domains, (list, tuple)) or not domains or any(not isinstance(d, str) or not d for d in domains):
            raise ValueError("web_allowed_domains_required")
        requested = args.get("requested_domains")
        if not isinstance(requested, (list, tuple)) or not requested or any(not isinstance(d, str) or not d for d in requested):
            raise ValueError("web_requested_domains_required")
        if not set(requested).issubset(set(domains)):
            raise ValueError("web_domain_scope_exceeded")


class ExternalReadAuthorizer:
    """Governance-owned issuer for exactly the three typed Phase 1B actions."""

    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate) -> None:
        self._evidence, self._issuer, self._approval = evidence, issuer, approval

    def canonicalize(self, proposal: Any) -> CanonicalAction:
        if proposal.action not in EXTERNAL_READ_ACTIONS:
            raise ValueError("unknown_external_action")
        if proposal.provenance is None or proposal.provenance.source not in {"user", "agent"}:
            raise ValueError("non_authoritative_provenance")
        resource = _obj(str(proposal.args.get("resource", "")), "resource")
        request = _obj(str(proposal.args.get("request", "")), "request")
        _validate_descriptor(proposal.action, resource, request)
        return CanonicalAction(
            proposal.intent_id, proposal.action, proposal.action,
            _canon(resource), (("request", _canon(request)),), proposal.provenance,
        )

    def authorize(self, proposal: Any) -> AuthorizedAction:
        canon = self.canonicalize(proposal)
        policy = PolicyDecision(Decision.ALLOW, "external_read_typed_descriptor")
        approval = self._approval.decide(canon, RiskLevel.MEDIUM, policy)
        budget = Decision.ALLOW
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(Event(EventKind.AUTHORIZATION, proposal.intent_id, {
            "action": canon.action, "opcode": canon.opcode, "resource": canon.resource,
            "args": [list(p) for p in canon.args], "digest": canon.digest(),
            "resource_identity": None, "resource_binding_id": None,
            "policy_decision": policy.decision.value, "risk": RiskLevel.MEDIUM.value,
            "approval_decision": approval.decision.value, "budget_decision": budget.value,
            "final_decision": Decision.ALLOW.value,
        }))
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)
