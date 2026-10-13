# IAOS — ExecutionEnvelope & Authorization Correspondence Contract

**Status:** FROZEN (Phase 0) — `EXECUTION_ENVELOPE_CONTRACT = FROZEN`
**Scope:** IAOS `AEPAdapter` → AEP effect boundary, `fs.read` on Windows/NTFS.
**AEP kernel modification:** AUTHORIZED. `AEP_KERNEL_INTEGRATION_GATE = PENDING`.
**Not claimed:** `CROSS_ADAPTER_REPLAY_GATE`, `DURABLE_EVIDENCE_GATE`, `CRASH_RECOVERY_GATE` (all PENDING), `RELEASE_FREEZE = NO`.

Implementation: `governance/envelope.py`, `auth_registry.py`, `consumption.py`,
`aep_runtime.py`, `aep_adapter.py`, `exec_observer.py`. Evidence of the contract:
`tests/test_envelope_contract.py` (C11–C23, order, purity, immutability) and
`tests/test_envelope_mutants.py` (each mutant killed by a named test).

## 1. Three separate properties (never collapsed)

```text
authorizes_static(A, E)   pure, decidable BEFORE the effect
consume_once(A)           atomic, stateful
effect_conforms(E, A, R)  audit of what really happened (NOT the safety control)
```

## 2. ExecutionEnvelope (closed schema)

```text
ExecutionEnvelope {
    schema_version        (= 1)
    execution_id
    authorization_id
    intent_id
    opcode                (= A.action, e.g. "fs.read")
    authorized_args       (list of [str, str] pairs)
    resource_binding_id   (required for fs.read; null only if an operation's schema declares NOT_APPLICABLE)
    authorization_digest
}
```

- Authority-bearing fields: `opcode`, `authorized_args`, `resource_binding_id`.
- Correlation-only fields (grant nothing): `schema_version`, `execution_id`, `authorization_id`, `intent_id`, `authorization_digest`.
- The schema is CLOSED. Unknown, missing or mistyped field (including nested) → `invalid_envelope`; unsupported version → `unsupported_schema`. No "runtime metadata" channel exists in Phase 0.
- The envelope never carries prompt, reasoning, instructions, memory, policy/risk/approval explanation, code, callables, module names or handlers.

## 3. AuthorizationRecord, registry and capabilities

- `AuthorizationRecord` is a frozen dataclass of immutable values. **Phase 0: no TTL, no expiration, no revocation after issue.** Records are never replaced.
- Governance holds `AuthorizationIssuer` (`issue` only). The AEP holds `AuthorizationView` (`get_issued` only): a record that is not `ISSUED` (PENDING / REVOKED / DENIED) is indistinguishable from a missing one.
- The adapter holds neither an issuer nor a consumption handle. `create_authorization_registry()` returns the two capabilities and keeps the store private.
- `AUTHORIZATION_AUTHENTICITY_SCOPE = in-process architectural boundary`. `CRYPTOGRAPHIC_AUTHORIZATION_AUTHENTICITY = NOT CLAIMED`. `MALICIOUS_SAME_PROCESS_ISOLATION = NOT CLAIMED`.

## 4. authorizes_static(A, E) — pure

Reads no clock, state, filesystem or ledger. True iff all hold:

```text
schema versions supported            A.execution_cardinality == SINGLE_USE
E.authorization_id == A.authorization_id
E.intent_id        == A.intent_id
E.opcode           == A.action
canonical_bytes(E.authorized_args) == canonical_bytes(A.canonical_args)
E.resource_binding_id == A.resource_binding_id
E.authorization_digest == authority_digest(A)
```

`authority(x) := (action|opcode, canonical_args|authorized_args, resource_binding_id)` and Phase 0 requires exact equality, not subset. No partial match is authorization. Narrowing semantics need a new contract.

## 5. Canonical serialization and digest

- One serializer: RFC 8785 / JCS, UTF-8, restricted domain: object, array, string, integer (|n| ≤ 2^53−1), boolean, null. Floats, non-finite numbers, lone surrogates, non-string keys and duplicate keys are rejected.
- `authorization_digest = SHA-256(JCS({schema_version, authorization_id, intent_id, action, canonical_args, resource_binding_id, execution_cardinality}))`, computed by the single function `authority_digest`, used by both the record and the adapter.
- The digest detects **divergence between what the adapter sent and what the registry holds**. It is not authentication: `hash != signature`. A valid digest alone never means an authentic authorization.

## 6. Normative order (AEP VALIDATE → EXEC)

```text
1 receive envelope
2 closed-schema validation        (no lookup, no consume)
3 AuthorizationView.get_issued(id)
4 authorizes_static(A, E)         (failure => nothing consumed)
5 registry / effect validation    (closed executor registry, args shape, binding present; still before consume)
6 ConsumptionLedger.consume_once  (atomic compare-and-set AVAILABLE -> CONSUMED, keyed by authorization_id)
7 handoff -> EXEC -> effect on the BOUND resource
8 observables
```

No later step may precede an earlier one. Never "check not consumed, then consume later". Any failure before step 6 → effect count 0.

**After step 6** the authorization stays `CONSUMED` whatever happens (handoff failure, handler failure, exception). Nothing un-consumes; there is no rollback. Phase 0 prefers "no duplicate authority" over automatic retry. Safe retry needs a separate idempotency/crash-recovery contract.

## 7. fs.read: effect safety by construction

- The file is opened once, during authorization (open-first); identity and final path are read from the open handle; only an opaque `resource_binding_id` travels.
- EXEC takes the binding (single use) and reads **the same handle**. It never opens, resolves or stats a path. The textual path has no authority after binding.
- `effect_conforms(E, A, R)` is an audit predicate over observables: `path_open_calls_during_exec == 0`, `bound_handle_reads == 1`, `observed_resource_identity == A.resource_identity`, matching `execution_id` and binding id. It proves what happened; it is not the control that prevents an unauthorized read.
- `path_open_calls_during_exec` sums the interpreter audit hook (`open` events of the thread inside the exec window) and `BindingTable.open_calls`.

## 8. Scope of replay protection

`consume_once` is an in-memory CAS under a lock: **single runtime / single process only**.

```text
SINGLE_RUNTIME_CONSUMPTION   = PASS
CROSS_ADAPTER_REPLAY_GATE    = PENDING
CROSS_PROCESS_REPLAY_GATE    = PENDING
RESTART_SAFE_CONSUMPTION     = PENDING
DURABLE_CONSUMPTION          = PENDING
```

Cross-adapter / cross-process / restart replay is **not proven**. Do not read this as production-wide exactly-once execution.

## 9. Known limitation (non-blocking)

```text
PRE_CONSUMPTION_BINDING_DOS = KNOWN_LIMITATION
SAFETY_IMPACT               = NO unauthorized effect
AVAILABILITY_IMPACT         = POSSIBLE
```

An envelope refused before `consume_once` may release the binding, so a hostile or defective translation can make a not-yet-consumed authorization unusable. No lifecycle, rebind or automatic recovery is implemented. (A `replay_denied` loser never releases the binding: it belongs to the winning execution.)

## 9b. Trust responsibilities

| Component | Owns | Does not |
|---|---|---|
| Governance | issuing authorizations | consume executions |
| Adapter | `AuthorizedAction` → `ExecutionEnvelope` translation, evidence precondition, result evidence | issue, consume, arbitrate |
| AEP VALIDATE | schema, lookup, `authorizes_static`, effect validation, atomic consume | |
| AEP EXEC | run the validated, consumed envelope through the closed executor registry | choose handlers from payload |

## 10. Required tests (all present)

C11 extra field · C12 digest mismatch · C13 not found · C13B not issued · C14 sequential replay · C15 concurrent consume (real threads) · C16A effect failure burns · C16B handoff failure burns · C17 metadata cannot change target · C18 adapter has no issuer · C19 adapter has no consumption handle · C20 JCS equivalence · C21 authority field change · C22 effect uses bound handle · C23 reopen-by-path observable.

Mutants, each killed by its named test: `M_EXTRA_FIELD_IGNORED`, `M_DIGEST_NOT_CHECKED`, `M_METADATA_CHANGES_TARGET`, `M_ADAPTER_GETS_ISSUER`, `M_ADAPTER_GETS_CONSUMPTION_HANDLE`, `M_CHECK_THEN_CONSUME`, `M_CONSUME_BEFORE_STATIC`, `M_UNCONSUME_ON_EFFECT_FAILURE`, `M_EFFECT_REOPEN_BY_PATH`.
