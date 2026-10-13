"""Typed, snapshot-bound authorities for the Computer Runtime V1."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .authorization import issue_authorized_action
from .contract import AuthorizedAction, CanonicalAction, Decision, PolicyDecision, Proposal, RiskLevel
from .evidence import Event, EventKind


INTERACTION_ACTIONS = ("browser.click", "browser.type", "browser.key", "browser.select_option")
SELECT_ROLES = ("combobox", "listbox")
MAX_OPTION_CHARS = 256


class ComputerInteractionAuthorizer:
    """Authorize one exact effect against one page/session/snapshot identity."""

    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate) -> None:
        self._evidence, self._issuer, self._approval = evidence, issuer, approval

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if proposal.action not in INTERACTION_ACTIONS:
            raise ValueError("unknown_computer_interaction")
        if proposal.provenance is None or proposal.provenance.source not in {"user", "agent"}:
            raise ValueError("non_authoritative_provenance")
        page = proposal.args.get("page_url")
        session = proposal.args.get("browser_session_id")
        snapshot = proposal.args.get("snapshot_id")
        _validate_page(page)
        if not isinstance(session, str) or not session:
            raise ValueError("browser_session_id_required")
        if not isinstance(snapshot, int) or snapshot < 1:
            raise ValueError("snapshot_id_required")
        values = {"ref": proposal.args.get("ref"), "text": proposal.args.get("text"), "key": proposal.args.get("key"),
                  "option": proposal.args.get("option"), "ref_role": proposal.args.get("ref_role")}
        if proposal.action in {"browser.click", "browser.type", "browser.select_option"}:
            if not isinstance(values["ref"], str) or not values["ref"]:
                raise ValueError("element_ref_required")
        if proposal.action == "browser.type" and not isinstance(values["text"], str):
            raise ValueError("type_text_required")
        if proposal.action == "browser.select_option":
            # ref_role is injected by the application from the trusted observation, never by the model.
            if values["ref_role"] not in SELECT_ROLES:
                raise ValueError("select_target_not_select_like")
            if not isinstance(values["option"], str) or not values["option"].strip():
                raise ValueError("select_option_required")
            if len(values["option"]) > MAX_OPTION_CHARS:
                raise ValueError("select_option_too_large")
        if proposal.action == "browser.key" and (not isinstance(values["key"], str) or not values["key"]):
            raise ValueError("key_required")
        if proposal.action == "browser.key" and values["ref"] is not None and not isinstance(values["ref"], str):
            raise ValueError("element_ref_invalid")
        args = [("browser_session_id", session), ("snapshot_id", str(snapshot))]
        if proposal.action in {"browser.click", "browser.type", "browser.select_option"} or values["ref"] is not None:
            args.append(("ref", values["ref"]))
        elif proposal.action == "browser.key":
            # Keep one closed envelope shape for page-level and element-level key presses.
            args.append(("ref", ""))
        if proposal.action == "browser.type":
            if len(values["text"]) > 4096:
                raise ValueError("type_text_too_large")
            args.append(("text", values["text"]))
        if proposal.action == "browser.select_option":
            args.append(("option", values["option"]))
            args.append(("ref_role", values["ref_role"]))
        if proposal.action == "browser.key":
            if len(values["key"]) > 64:
                raise ValueError("key_too_large")
            args.append(("key", values["key"]))
        args.sort()
        return CanonicalAction(proposal.intent_id, proposal.action, proposal.action, page, tuple(args), proposal.provenance, session)

    def authorize(self, proposal: Proposal) -> AuthorizedAction:
        canon = self.canonicalize(proposal)
        policy = PolicyDecision(Decision.ALLOW, "computer_interaction_typed_effect")
        approval = self._approval.decide(canon, RiskLevel.LOW, policy)
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(Event(EventKind.AUTHORIZATION, proposal.intent_id, {
            "action": canon.action, "opcode": canon.opcode, "resource": canon.resource,
            "args": [list(pair) for pair in canon.args], "digest": canon.digest(),
            "resource_identity": canon.resource_identity, "resource_binding_id": None,
            "policy_decision": policy.decision.value, "risk": RiskLevel.LOW.value,
            "approval_decision": approval.decision.value, "budget_decision": Decision.ALLOW.value,
            "final_decision": Decision.ALLOW.value,
        }))
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)


def _validate_page(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("page_url_invalid")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("https_public_url_required")
