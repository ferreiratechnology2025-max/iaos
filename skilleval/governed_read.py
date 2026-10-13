"""Read-only context (text file or raw document bytes) for a task, obtained ONLY through the existing governance pipeline.

  relative path -> Proposal(fs.read, provenance=user) -> Authorizer (canonicalize open-first -> policy -> risk -> approval
  -> budget -> AUTHORIZATION evidence) -> AuthorizedAction -> AEPAdapter -> AEPRuntime (consume once) -> RealFsReadExecutor
  reading from the handle bound at authorization time -> AdapterResult.output (text)
  (read_bytes: the same chain with fs.read_bytes -> AdapterResult.binary_output, the raw bytes of the bound object)

This module only composes the public governance components, wired as tests/conftest.py wires them. It adds no authority
model and no policy, and it never opens, stats or lists a path: it submits a Proposal and receives text. Anything the
policy denies (outside the workspace root, unknown source, missing or non-regular file) is rejected and nothing is read.
The returned text is DATA for cognition; it is never fed back as a Proposal or as authority.

KNOWN LIMITS (governance is unchanged, so they are inherited): the fs.read executor has no size limit (this module only
refuses to forward oversized content to the model); evidence is in-memory per process; certified scope is Windows/NTFS.
"""
import threading
import uuid
from dataclasses import dataclass
from typing import Optional

from governance.aep_adapter import AdapterError, AEPAdapter
from governance.aep_executors import default_registry
from governance.aep_runtime import AEPRuntime
from governance.approval import ApprovalGate
from governance.auth_registry import create_authorization_registry
from governance.authorization import Authorizer
from governance.budget import Budget
from governance.canonicalize import Canonicalizer, ResourceResolver
from governance.consumption import create_consumption_ledger
from governance.contract import Decision, Proposal, Provenance
from governance.evidence import InMemoryEvidenceStore
from governance.handle_binding import BindingTable
from governance.policy import Policy

MAX_PATH_CHARS = 1024
MAX_FILE_CHARS = 100_000
MAX_DOCUMENT_BYTES = 10_000_000  # bridge cap on what it will forward/parse after the (unbounded, inherited) governed read


class ReadDenied(Exception):
    """Governance (or input validation) refused the read. ``reason`` is a stable, content-free code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReadFailed(Exception):
    """The read was authorized but could not produce text (e.g. not valid UTF-8)."""


class FileTooLarge(Exception):
    pass


@dataclass(frozen=True)
class GovernedFile:
    relative: str
    content: str
    sha256: str
    chars: int
    authorization_event_id: str
    execution_id: str
    resource_identity: Optional[str]
    bound_handle_reads: Optional[int]
    path_open_calls_during_exec: Optional[int]


@dataclass(frozen=True)
class GovernedBytes:
    relative: str
    data: bytes
    sha256: str
    size: int
    authorization_event_id: str
    execution_id: str
    resource_identity: Optional[str]
    bound_handle_reads: Optional[int]
    path_open_calls_during_exec: Optional[int]


def _denial_reason(outcome) -> str:
    for part in (outcome.policy_decision, outcome.approval_decision, outcome.budget_decision, outcome.final_decision):
        if part.decision is not Decision.ALLOW:
            return str(part.reason)
    return "denied"


class GovernedFileReader:
    def __init__(self, workspace_root: str, max_chars: int = MAX_FILE_CHARS, max_bytes: int = MAX_DOCUMENT_BYTES) -> None:
        self._max_chars = max_chars
        self._max_bytes = max_bytes
        self.store = InMemoryEvidenceStore()
        self.bindings = BindingTable()
        resolver = ResourceResolver(workspace_root, self.bindings)
        self.root = resolver.root
        issuer, view = create_authorization_registry()
        _ledger, consume = create_consumption_ledger()
        self._authorizer = Authorizer(Canonicalizer(resolver), Policy(self.root), ApprovalGate(), Budget(), self.store, issuer)
        self.runtime = AEPRuntime(default_registry(), self.bindings, view, consume)
        self._adapter = AEPAdapter(self.runtime, bindings=self.bindings)
        self._lock = threading.Lock()  # one governed read at a time (BindingTable/ledger are single-runtime scope)

    def effect_log(self):
        return self._adapter.effect_log()

    def _authorized_execute(self, action_name: str, relative: str):
        if not isinstance(relative, str) or not relative.strip() or len(relative) > MAX_PATH_CHARS:
            raise ReadDenied("invalid_path")
        intent = "ctx-" + uuid.uuid4().hex[:12]
        with self._lock:
            outcome = self._authorizer.authorize(Proposal(intent, action_name, {"path": relative}, Provenance("user", intent)))
            action = outcome.authorized_action
            if action is None:
                raise ReadDenied(_denial_reason(outcome))
            try:
                return action, self._adapter.execute(action, self.store)
            except AdapterError as exc:
                raise ReadDenied("adapter:" + str(exc))

    def read(self, relative: str) -> GovernedFile:
        action, result = self._authorized_execute("fs.read", relative)
        if not result.ok or not isinstance(result.output, str):
            raise ReadFailed("unreadable")
        if len(result.output) > self._max_chars:
            raise FileTooLarge(f"{len(result.output)} chars")
        obs = result.observation
        return GovernedFile(
            relative=relative, content=result.output, sha256=result.result_digest, chars=len(result.output),
            authorization_event_id=action.authorization_id, execution_id=result.execution_id,
            resource_identity=result.resource_identity,
            bound_handle_reads=obs.bound_handle_reads if obs else None,
            path_open_calls_during_exec=obs.path_open_calls_during_exec if obs else None,
        )

    def read_bytes(self, relative: str) -> GovernedBytes:
        """Raw bytes of the object bound at authorization time (fs.read_bytes). This module never interprets them."""
        action, result = self._authorized_execute("fs.read_bytes", relative)
        if not result.ok or not isinstance(result.binary_output, bytes):
            raise ReadFailed("unreadable")
        if len(result.binary_output) > self._max_bytes:
            raise FileTooLarge(f"{len(result.binary_output)} bytes")
        obs = result.observation
        return GovernedBytes(
            relative=relative, data=result.binary_output, sha256=result.result_digest, size=len(result.binary_output),
            authorization_event_id=action.authorization_id, execution_id=result.execution_id,
            resource_identity=result.resource_identity,
            bound_handle_reads=obs.bound_handle_reads if obs else None,
            path_open_calls_during_exec=obs.path_open_calls_during_exec if obs else None,
        )
