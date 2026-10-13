"""Offline Delivery V1A: one approved, self-only Daily Brief email effect."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from application.daily_brief_history import DailyBriefHistoryStore, DailyBriefRun
from governance.aep_adapter import AEPAdapter
from governance.aep_executors import EffectRecord, Executor
from governance.approval import ApprovalGate
from governance.auth_registry import create_authorization_registry
from governance.consumption import create_consumption_ledger
from governance.contract import Proposal, Provenance
from governance.external_write import EmailSendAuthorizer, EmailSendDescriptor
from governance.handle_binding import BindingTable
from governance.aep_executors import AEPExecutorRegistry
from governance.aep_runtime import AEPRuntime


def _sao_paulo_timezone():
    try:
        return ZoneInfo("America/Sao_Paulo")
    except (KeyError, ZoneInfoNotFoundError):
        # The Windows bundle may not ship IANA tzdata. São Paulo has had no
        # DST since 2019; keep the current product timezone deterministic.
        return timezone(timedelta(hours=-3), "America/Sao_Paulo")


@dataclass(frozen=True)
class DeliveryContent:
    subject: str
    body_text: str
    daily_brief_run_id: str
    content_digest: str


def format_daily_brief_email(run: DailyBriefRun) -> DeliveryContent:
    if run.status != "success":
        raise ValueError("daily_brief_not_successful")
    try:
        completed = datetime.fromisoformat(run.completed_at.replace("Z", "+00:00"))
        local_date = completed.astimezone(_sao_paulo_timezone())
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("invalid_daily_brief_completed_at") from exc
    months = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    display_date = f"{local_date.day} de {months[local_date.month - 1]} de {local_date.year}"
    subject = f"IAOS — Resumo diário — {local_date.day:02d}/{local_date.month:02d}/{local_date.year:04d}"
    body_parts = ["IAOS — Resumo diário", display_date, "", "RESUMO", _clean_plaintext(run.summary or "(nenhum)")]
    if run.scout_section and run.scout_section != "No new Scout findings.":
        body_parts.extend(["", "SCOUTS", _clean_plaintext(run.scout_section)])
    body_parts.extend(["", "FONTES"])
    source_labels = (("email.read", "E-mail"), ("calendar.read", "Agenda"), ("web.search", "Web"))
    body_parts.extend("- " + label for action, label in source_labels if any(ref.startswith(action + ":") for ref in run.source_refs))
    if run.scout_section and run.scout_section != "No new Scout findings.":
        body_parts.append("- Scouts")
    body = "\n".join(body_parts).strip() + "\n"
    digest = hashlib.sha256((subject + "\n\n" + body).encode("utf-8")).hexdigest()
    return DeliveryContent(subject, body, run.run_id, digest)


def _clean_plaintext(value: str) -> str:
    """Remove presentation syntax; never attempts to repair legacy U+FFFD."""
    value = value.replace("\\*", "*").replace("\\_", "_").replace("\\`", "`")
    value = value.replace("**", "").replace("__", "")
    lines = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines) or "(nenhum)"


@dataclass(frozen=True)
class DeliveryProposal:
    delivery_id: str
    daily_brief_run_id: str
    provider_ref: str
    account_ref: str
    to: str
    subject: str
    body_text: str
    content_digest: str


@dataclass(frozen=True)
class DeliveryAttempt:
    delivery_id: str
    execution_id: str
    daily_brief_run_id: str
    recipient: str
    content_digest: str
    created_at: float
    status: str = "attempting"


@dataclass(frozen=True)
class DeliveryReceipt:
    delivery_id: str
    execution_id: str
    daily_brief_run_id: str
    provider_ref: str
    provider_message_id: str
    recipient: str
    accepted_at: float
    status: str = "provider_accepted"


class InMemoryDeliveryStore:
    def __init__(self, fail_receipt: bool = False) -> None:
        self.attempts: dict[str, DeliveryAttempt] = {}
        self.receipts: dict[str, DeliveryReceipt] = {}
        self.fail_receipt = fail_receipt

    def save_attempt(self, attempt: DeliveryAttempt) -> None:
        self.attempts[attempt.delivery_id] = attempt

    def get_attempt(self, delivery_id: str) -> Optional[DeliveryAttempt]:
        return self.attempts.get(delivery_id)

    def save_receipt(self, receipt: DeliveryReceipt) -> None:
        if self.fail_receipt:
            raise OSError("receipt_persistence_failed")
        self.receipts[receipt.delivery_id] = receipt

    def find_receipt(self, run_id: str, recipient: str) -> Optional[DeliveryReceipt]:
        return next((r for r in self.receipts.values() if r.daily_brief_run_id == run_id and r.recipient == recipient), None)


class FakeEmailSendError(Exception):
    pass


class FakeEmailSendExecutor(Executor):
    action = "email.send"
    opcode = "EMAIL_SEND"
    arg_names = ("request",)
    binds_identity = False

    def __init__(self, failure: Optional[str] = None, on_effect: Any = None) -> None:
        self.failure = failure
        self.on_effect = on_effect
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.mimes: list[bytes] = []
        self._effects: list[EffectRecord] = []

    def run(self, resource, args, identity, binding=None):
        descriptor = json.loads(resource)
        request = json.loads(dict(args)["request"])
        EmailSendDescriptor(descriptor, request)
        message = EmailMessage()
        message["To"] = request["to"]
        message["Subject"] = request["subject"]
        message.set_content(request["body_text"])
        mime = message.as_bytes()
        self.requests.append((resource, request))
        self.mimes.append(mime)
        if self.on_effect:
            self.on_effect()
        if self.failure:
            raise FakeEmailSendError(self.failure)
        provider_message_id = "fake-message-" + hashlib.sha256(mime).hexdigest()[:12]
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), hashlib.sha256(mime).hexdigest()))
        return type("FakeWriteResult", (), {"content": {"provider_message_id": provider_message_id}, "identity": None, "result_authority": "EFFECT", "result_provenance": "FAKE_PROVIDER"})()

    def effects(self):
        return tuple(self._effects)


class DeliveryService:
    def __init__(self, history: DailyBriefHistoryStore, broker: Any, store: InMemoryDeliveryStore,
                 authorizer: EmailSendAuthorizer, approval: ApprovalGate, adapter: AEPAdapter,
                 evidence: Any, clock: Any, issuer: Any) -> None:
        self.history, self.broker, self.store = history, broker, store
        self.authorizer, self.approval, self.adapter, self.evidence = authorizer, approval, adapter, evidence
        self.clock, self.issuer = clock, issuer

    def propose(self, run_id: str, delivery_id: str) -> DeliveryProposal:
        run = self.history.get(run_id)
        if run is None:
            raise ValueError("daily_brief_not_found")
        content = format_daily_brief_email(run)
        record = self.broker._store.load()
        if record is None:
            raise PermissionError("google_disconnected")
        return DeliveryProposal(delivery_id, run_id, "google.gmail.v1", record.account_ref, record.email,
                                content.subject, content.body_text, content.content_digest)

    def _proposal(self, proposal: DeliveryProposal) -> Proposal:
        return Proposal(
            intent_id="delivery:" + proposal.delivery_id, action="email.send",
            args={"resource": json.dumps({"provider_ref": proposal.provider_ref, "account_ref": proposal.account_ref, "recipient": proposal.to}, sort_keys=True, separators=(",", ":")),
                  "request": json.dumps({"to": proposal.to, "subject": proposal.subject, "body_text": proposal.body_text, "daily_brief_run_id": proposal.daily_brief_run_id}, sort_keys=True, separators=(",", ":"))},
            provenance=Provenance("user", "delivery:" + proposal.delivery_id),
        )

    def approve(self, proposal: DeliveryProposal, approver_id: str = "human") -> None:
        canon = self.authorizer.canonicalize(self._proposal(proposal))
        self.approval.grant(approver_id, canon.intent_id, canon.digest())

    def send(self, proposal: DeliveryProposal) -> Any:
        if self.store.find_receipt(proposal.daily_brief_run_id, proposal.to):
            raise PermissionError("delivery_already_completed")
        if self.store.get_attempt(proposal.delivery_id):
            raise PermissionError("delivery_ambiguous_or_already_attempted")
        action = self.authorizer.authorize(self._proposal(proposal))
        execution_id = "x-" + hashlib.sha256((action.authorization_id + ":" + action.digest).encode()).hexdigest()[:16]
        self.store.save_attempt(DeliveryAttempt(proposal.delivery_id, execution_id, proposal.daily_brief_run_id,
                                                proposal.to, proposal.content_digest, self.clock()))
        result = self.adapter.execute(action, self.evidence)
        if not result.ok:
            return result
        receipt = DeliveryReceipt(proposal.delivery_id, result.execution_id, proposal.daily_brief_run_id,
                                  proposal.provider_ref, result.structured_output["provider_message_id"], proposal.to, self.clock())
        self.store.save_receipt(receipt)
        return receipt

    def state(self, delivery_id: str) -> str:
        if delivery_id in self.store.receipts:
            return "provider_accepted"
        if delivery_id in self.store.attempts:
            return "ambiguous"
        return "not_started"


def build_offline_delivery_service(history, broker, store, provider, clock=lambda: 1.0):
    from governance.evidence import InMemoryEvidenceStore
    evidence = InMemoryEvidenceStore()
    approval = ApprovalGate()
    issuer, view = create_authorization_registry()
    ledger, consume = create_consumption_ledger()
    runtime = AEPRuntime(AEPExecutorRegistry({"email.send": provider}), BindingTable(), view, consume)
    adapter = AEPAdapter(runtime)
    authorizer = EmailSendAuthorizer(evidence, issuer, approval, lambda: (broker._store.load().account_ref, broker._store.load().email))
    return DeliveryService(history, broker, store, authorizer, approval, adapter, evidence, clock, issuer), provider
