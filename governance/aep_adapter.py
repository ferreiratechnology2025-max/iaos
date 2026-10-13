"""AEP adapter: translates ONE AuthorizedAction into ONE ExecutionEnvelope.

It is the only route to the runtime and it has no policy. It is NOT the arbiter
of authorization: the AEP looks the authorization up in its own read-only view
and compares (``authorizes_static``) before it atomically consumes it. The
adapter may build an envelope; it cannot make an authorization valid, cannot
issue one and cannot consume one. It holds neither an AuthorizationIssuer nor a
ConsumptionHandle.

Before translating it still checks the evidence precondition: the
AUTHORIZATION event exists, is ALLOW and matches (intent, opcode, resource,
args, digest). Evidence is a separate requirement from authorization.

Replay protection lives in the AEP's ConsumptionLedger (single runtime scope).
CROSS_ADAPTER / CROSS_PROCESS / RESTART replay protection: PENDING.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .contract import AuthorizedAction, action_digest
from .envelope import EnvelopeRefused, ExecutionEnvelope, SCHEMA_VERSION, authority_digest
from .evidence import Event, EventKind
from .exec_observer import EffectExecutionObservation
from .handle_binding import BindingTable


class AdapterError(Exception):
    pass


@dataclass(frozen=True)
class AdapterResult:
    ok: bool
    output: Optional[str]
    execution_id: str
    result_digest: Optional[str]
    error: Optional[str]
    resource_identity: Optional[str] = None  # identity actually read
    observation: Optional[EffectExecutionObservation] = None
    binary_output: Optional[bytes] = None  # fs.read_bytes: the raw bytes; ``output`` is None then
    structured_output: Any = None
    result_authority: Optional[str] = None
    result_provenance: Optional[str] = None


class AEPSpy:
    """Test double around the AEP port. ``envelopes`` is what the adapter
    submitted; ``calls`` is what actually reached the effect boundary (BOOT /
    EXEC / HALT as logged by the runtime itself)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.envelopes: List[Any] = []

    @property
    def calls(self) -> List[tuple]:
        return self._inner.calls

    def submit(self, envelope: Any) -> Any:
        self.envelopes.append(envelope)
        return self._inner.submit(envelope)

    def effect_log(self) -> List[Dict[str, Any]]:
        return self._inner.effect_log()

    def observations(self) -> List[EffectExecutionObservation]:
        return self._inner.observations()


class AEPAdapter:
    def __init__(
        self,
        aep: Any,
        bindings: Optional[BindingTable] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._bindings = bindings if bindings is not None else BindingTable()
        self._aep = aep  # AEP port: ``submit`` only
        self._clock = clock

    def effect_log(self) -> List[Dict[str, Any]]:
        """Observable real effects, as plain data (for the oracle)."""
        return self._aep.effect_log()

    def _verify_against_evidence(self, action: AuthorizedAction, store: Any) -> None:
        auth = None
        for ev in store.events():
            if ev.get("kind") == "AUTHORIZATION" and ev.get("event_id") == action.authorization_id:
                auth = ev
                break
        if auth is None:
            raise AdapterError("authorization_not_in_evidence")
        for key in ("final_decision", "policy_decision", "approval_decision", "budget_decision"):
            if auth.get(key) != "ALLOW":
                raise AdapterError("authorization_not_allow")
        authority = [list(p) for p in action.args]
        expected = (
            action.intent_id, action.opcode, action.resource, authority,
            action.digest, action.resource_identity, action.resource_binding_id,
        )
        recorded = (
            auth.get("intent_id"),
            auth.get("opcode"),
            auth.get("resource"),
            auth.get("args"),
            auth.get("digest"),
            auth.get("resource_identity"),
            auth.get("resource_binding_id"),
        )
        if expected != recorded:
            raise AdapterError("authorization_mismatch")
        if action.digest != action_digest(
            action.intent_id, action.action, action.opcode, action.resource,
            action.args, action.resource_identity,
        ):
            raise AdapterError("digest_mismatch")

    def _translate(self, action: AuthorizedAction) -> ExecutionEnvelope:
        """Pure translation. Every authority-bearing field comes from ``action``
        and nothing else: it never creates, infers, substitutes or enlarges."""
        execution_id = "x-" + hashlib.sha256(
            ("%s:%s" % (action.authorization_id, action.digest)).encode("utf-8")
        ).hexdigest()[:16]
        return ExecutionEnvelope(
            schema_version=SCHEMA_VERSION,
            execution_id=execution_id,
            authorization_id=action.authorization_id,
            intent_id=action.intent_id,
            opcode=action.action,
            authorized_args=tuple(action.args),
            resource_binding_id=action.resource_binding_id,
            authorization_digest=authority_digest(
                action.authorization_id,
                action.intent_id,
                action.action,
                action.args,
                action.resource_binding_id,
            ),
        )

    def _dispatch(self, envelope: ExecutionEnvelope, action: AuthorizedAction) -> Any:
        return self._aep.submit(envelope)

    def execute(self, action: AuthorizedAction, store: Any) -> AdapterResult:
        if not isinstance(action, AuthorizedAction):
            raise TypeError("adapter accepts only AuthorizedAction")
        self._verify_against_evidence(action, store)
        envelope = self._translate(action)
        started = self._clock()
        ok, output, error, executed_identity, observation = True, None, None, None, None
        binary_output = None
        structured_output = None
        result_authority = None
        result_provenance = None
        release = True
        try:
            outcome = self._dispatch(envelope, action)
            if isinstance(outcome.content, bytes):  # bytes stay bytes: no str representation of them exists
                binary_output = outcome.content
            elif isinstance(outcome.content, str):
                output = outcome.content
            else:
                structured_output = outcome.content
            result_authority = outcome.result_authority
            result_provenance = outcome.result_provenance
            executed_identity = outcome.identity
            observation = outcome.observation
        except EnvelopeRefused as exc:  # refused BEFORE any effect
            if exc.code == "replay_denied":
                release = False  # the binding belongs to the execution that won the consumption
            raise AdapterError(exc.code)
        except Exception as exc:  # after the authorization was consumed: recorded, never swallowed silently
            ok, error = False, repr(exc)
        finally:
            if release:
                self._bindings.release(action.resource_binding_id)  # no-op when EXEC already took it
        finished = self._clock()
        result_digest = (
            hashlib.sha256(output.encode("utf-8")).hexdigest()
            if ok and isinstance(output, str)
            else hashlib.sha256(binary_output).hexdigest()
            if ok and binary_output is not None
            else None
        )
        store.append(
            Event(
                EventKind.EXECUTION_RESULT,
                action.intent_id,
                {
                    "execution_id": envelope.execution_id,
                    "authorization_id": action.authorization_id,
                    "action": action.action,
                    "opcode": action.opcode,
                    "resource": action.resource,
                    "args": [list(p) for p in action.args],
                    "started_at": started,
                    "finished_at": finished,
                    "ok": ok,
                    "result_digest": result_digest,
                    "resource_identity": executed_identity,
                    "error": error,
                    "result_authority": result_authority,
                    "result_provenance": result_provenance,
                    "bound_handle_reads": observation.bound_handle_reads if observation else None,
                    "path_open_calls_during_exec": (
                        observation.path_open_calls_during_exec if observation else None
                    ),
                },
            )
        )
        return AdapterResult(
            ok, output, envelope.execution_id, result_digest, error, executed_identity, observation,
            binary_output, structured_output, result_authority, result_provenance,
        )
