"""AEP runtime: the effect boundary. VALIDATE -> consume -> EXEC.

Normative order (nothing later may precede anything earlier):

  1. receive envelope
  2. closed-schema validation               (no lookup, no consume)
  3. AuthorizationView.get_issued(id)       (only ISSUED records exist for us)
  4. authorizes_static(A, E)                (pure; failure => nothing consumed)
  5. registry / effect validation           (still before consuming)
  6. atomic consume_once                    (the transition itself decides)
  7. handoff -> EXEC -> effect on the BOUND resource
  8. observables (effect_conforms is audit, not the control)

The AEP does not trust the adapter: it looks the authorization up itself and
compares. After step 6 the authorization is burned. A failure in the handoff or
in the effect NEVER makes it available again (fail-closed; no retry in Phase 0).

The AEP holds only an ``AuthorizationView`` and a ``ConsumptionHandle``. It
never holds an issuer.

Scope note: this is the IAOS effect boundary. The reference kernel in the
separate AEP project is a state machine whose EXEC produces no side effects; it
is not integrated here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .aep_executors import AEPExecutorRegistry, Executor, UnknownExecutor
from .consumption import ConsumeResult
from .envelope import EnvelopeRefused, ExecutionEnvelope, static_mismatch, validate_envelope
from .exec_observer import EffectExecutionObservation, ExecutedAuthorityObservation, ExecWindow
from .handle_binding import BindingError, BindingTable


class ExecRefused(Exception):
    """Failure AFTER the authorization was consumed. The authorization stays consumed."""


@dataclass(frozen=True)
class ExecOutcome:
    content: Any
    identity: Optional[str]
    observation: EffectExecutionObservation
    result_authority: Optional[str] = None
    result_provenance: Optional[str] = None


class AEPRuntime:
    def __init__(
        self,
        registry: AEPExecutorRegistry,
        bindings: BindingTable,
        view: Any,
        consume: Any,
    ) -> None:
        self._registry = registry
        self._bindings = bindings
        self._view = view  # AuthorizationView: read-only
        self._consume = consume  # ConsumptionHandle: consume_once only
        self._booted = False
        self.calls: List[tuple] = []  # what actually reached the effect boundary
        self._observations: List[EffectExecutionObservation] = []

    # ---- observables -------------------------------------------------------------

    def observations(self) -> List[EffectExecutionObservation]:
        return list(self._observations)

    def effect_log(self) -> List[Dict[str, Any]]:
        return [e.as_dict() for e in self._registry.effects()]

    # ---- the pipeline ------------------------------------------------------------

    def submit(self, envelope: Any) -> ExecOutcome:
        env = validate_envelope(envelope)  # 2. closed schema
        record = self._view.get_issued(env.authorization_id)  # 3. independent lookup
        if record is None:
            raise EnvelopeRefused("no_valid_authorization")
        mismatch = static_mismatch(record, env)  # 4. pure
        if mismatch is not None:
            raise EnvelopeRefused(mismatch)
        executor = self._validate_effect(env, record)  # 5. registry / effect validation
        won = self._consume.consume_once(env.authorization_id, env.execution_id)  # 6. atomic
        if won is not ConsumeResult.CONSUMED:
            raise EnvelopeRefused("replay_denied")
        return self._exec(env, record, executor)  # 7-8. past here the authorization stays consumed

    def _validate_effect(self, env: ExecutionEnvelope, record: Any) -> Executor:
        try:
            executor = self._registry.lookup(env.opcode)  # closed registry, no fallback
        except UnknownExecutor:
            raise EnvelopeRefused("unknown_executor")
        if executor.opcode != record.opcode:
            raise EnvelopeRefused("opcode_mismatch")
        if tuple(k for k, _ in env.authorized_args) != executor.arg_names:
            raise EnvelopeRefused("args_shape")
        if executor.binds_identity and not (
            record.resource and record.resource_identity and env.resource_binding_id
        ):
            raise EnvelopeRefused("identity_required")
        return executor

    def _handoff(self) -> None:
        self._booted = True
        self.calls.append(("BOOT",))

    def _exec(self, env: ExecutionEnvelope, record: Any, executor: Executor) -> ExecOutcome:
        executor.begin_execution(env.execution_id)
        self._handoff()
        try:
            binding = None
            if executor.binds_identity:
                try:
                    binding = self._bindings.take(env.resource_binding_id)
                except BindingError as exc:
                    raise ExecRefused(str(exc))
            args = tuple(env.authorized_args)
            executed = ExecutedAuthorityObservation(  # observation of what reached the effect; decides nothing
                authorization_id=env.authorization_id,
                intent_id=env.intent_id,
                opcode=executor.opcode,
                executed_args=args,
                resource_binding_id=env.resource_binding_id,
                authorization_digest=env.authorization_digest,
            )
            self.calls.append(("EXEC", executor.opcode, record.resource, args))
            reads_before = len(self._bindings.read_log())
            opens_before = self._bindings.open_calls()
            raw = None
            try:
                with ExecWindow() as window:
                    raw = executor.run(record.resource, args, record.resource_identity, binding)
            except BaseException:
                self._observe(env, window, reads_before, opens_before, None, executed)
                raise  # post-consume failure: authorization stays CONSUMED (no rollback)
            observation = self._observe(
                env, window, reads_before, opens_before, getattr(raw, "identity", None), executed
            )
            return ExecOutcome(
                getattr(raw, "content", raw), getattr(raw, "identity", None), observation,
                getattr(raw, "result_authority", None), getattr(raw, "result_provenance", None),
            )
        finally:
            executor.end_execution()
            self._booted = False
            self.calls.append(("HALT",))

    def _observe(self, env, window, reads_before, opens_before, identity, executed=None) -> EffectExecutionObservation:
        observation = EffectExecutionObservation(
            execution_id=env.execution_id,
            resource_binding_id=env.resource_binding_id,
            bound_handle_reads=len(self._bindings.read_log()) - reads_before,
            path_open_calls_during_exec=window.audit_opens + (self._bindings.open_calls() - opens_before),
            observed_resource_identity=identity,
            executed=executed,
        )
        self._observations.append(observation)
        return observation
