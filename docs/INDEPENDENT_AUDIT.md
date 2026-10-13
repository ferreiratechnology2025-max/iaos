# Independent (structural) audit - Phase 0

```
IAOS_INDEPENDENT_AUDIT_GATE   INDEPENDENCE_SCOPE=decision_logic_only
AUDIT_DECISION_INDEPENDENCE   = TARGET (demonstrated by tests below)
EVIDENCE_SOURCE_INDEPENDENCE  = PENDING
EVIDENCE_PRODUCER_TRUST       = REQUIRED_PHASE_0
AEP_INTEGRATION               = DEFERRED   (AEP_REQUIRED_FOR_PHASE_0 = NO)
```

## What it is
`audit/` is a layer parallel to `governance/`. It compares a **ClaimedResult**
(what the audited component says) with a **VerifiedResult** computed only from supplied
evidence. `silent_escape = claimed PASS AND verified != PASS` (so missing evidence or an
unsupported scope never preserves a nominal PASS).

```
Governance -> authority      Runtime -> effect      Auditor -> knowledge about what happened
```
An `AuditReport` is never authority: it cannot produce an `AuthorizedAction`, an envelope,
a consumption or an effect, and nothing in `governance/` imports `audit/`.

## Decision independence (what Phase 0 proves)
The auditor package: imports only the standard library and itself; references no
governance/runtime symbol (AST test); performs no `open/eval/exec`; receives only plain,
immutable, JSON-serializable DTOs (`ResourceBindingEvidence`); never uses the claimed status as
a metric input. These are AST/lint-of-intent tests, not isolation against hostile Python.

## What it does NOT prove
Evidence still comes from the producer (`path_open_calls_during_exec`, `bound_handle_reads`,
`observed_resource_identity` are produced by the runtime's own observer). If the producer
publishes false but internally consistent observables, this auditor may PASS. It is not
tamper-proof, attested, cryptographically independent or hostile-runtime-resistant. Future
options (OS-level observation, separate-process observer, append-only external trace,
attestation) are out of scope.

## MetricResult
`status` in `PASS | FAIL | UNSUPPORTED | INSUFFICIENT_EVIDENCE`; `UNSUPPORTED != FAIL`,
`INSUFFICIENT_EVIDENCE != FAIL`, neither is ever PASS. Fields: metric, status, scope,
violations, observations.

## ResourceBindingMetric (the only metric in Phase 0)
`validated_scope = win32 / NTFS / fs.read`; anything else is UNSUPPORTED. Evaluation order:
scope facts missing -> INSUFFICIENT; scope differs -> UNSUPPORTED; required facts missing or
malformed (non-int/bool/negative counts, empty ids, non-bool `effect_succeeded`) ->
INSUFFICIENT; then PASS requires simultaneously `observed identity == authorized identity`,
`path_open_calls_during_exec == 0`, `bound_handle_reads == 1`, `effect_succeeded`.

## Tests (tests/test_independent_audit.py)
Adversarial A1 good bound read, A2 path reopen, A3 identity mismatch, A4 no bound read,
A5 missing observation, A6 honest failure, A7 unsupported scope; missing-field and malformed
parametrizations; serializability; structural AST tests; real-runtime runs (a correct run and
`ReopenByPathExecutor`); mutants `M_AUDITOR_ALWAYS_PASS` and `M_MISSING_AS_SAFE`, each killed
by named adversarial cases. The evidence *producer* lives in `tests/audit_support.py`.

## Not implemented (deliberately)
ExecutionOrder, ConsumptionIntegrity, EvidenceCompleteness and semantic/LLM metrics
(AuthorizationCorrespondence now exists: see `docs/AUTHORIZATION_CORRESPONDENCE.md`); durable evidence store; D:\Evidence and D:\AEP are not dependencies.

## Audit wiring (IAOS_AUDIT_WIRING_GATE)

```
IAOS_STRUCTURAL_AUDIT_GATE = PASS   (the auditor and ResourceBindingMetric)
IAOS_AUDIT_WIRING_GATE     = see the gate report (exporter in evidence/, real end-to-end tests)
```

Flow: `Governed Execution Runtime -> observables -> evidence.exporter -> immutable
ResourceBindingEvidence -> audit.ResourceBindingMetric -> AuditReport`.

- `evidence/types.py`: the neutral DTO, shared by exporter and auditor (stdlib only). `audit`
  imports only `evidence.types`; `evidence` imports neither `audit` nor `governance`; no cycle.
- `evidence/exporter.py`: observe -> normalize -> serialize. Reads the result/action by
  attribute (duck typing). It does not authorize, execute, evaluate a metric or declare a gate;
  an AST test allows only `is None` comparisons and no decision vocabulary. Absence stays `None`
  (never 0 / false / ""); the host filesystem is `None` when it cannot be determined.
- `governance/` imports neither `audit` nor `evidence` (AST test). The nominal claim
  (`ClaimedResult`) is still built in `tests/audit_support.py`; a product-side claim producer is not
  part of this gate.
- Metric order change: a present scope fact that differs from `win32/NTFS/fs.read` is UNSUPPORTED
  even if another scope fact is missing (e.g. Linux with an unknown filesystem).

### Exporter mutants
| mutant | result |
|---|---|
| `M_EXPORTER_DEFAULTS_MISSING_AS_SAFE` | KILLED (absence turned into a certified PASS) |
| `M_EXPORTER_HIDES_REOPEN` (only `path_open_calls` -> 0) | KILLED, by the redundant fact `bound_handle_reads == 0` |
| `M_EXPORTER_HIDES_REOPEN_CONSISTENT` (`path_open_calls` 0 and `bound_handle_reads` 1) | **SURVIVES_EXPECTED** |

The surviving mutant materializes `EVIDENCE_SOURCE_INDEPENDENCE=PENDING` and
`EVIDENCE_PRODUCER_TRUST=REQUIRED_PHASE_0`: a consistent lie by the producer is not detected.
No artificial mechanism was added to kill it.

## Checkpoint state and precedence

```
IAOS_STRUCTURAL_AUDIT_GATE   = PASS      IAOS_AUDIT_WIRING_GATE = PASS
AUDIT_DECISION_INDEPENDENCE  = PASS      INDEPENDENCE_SCOPE     = decision_logic_only
PRODUCTION_EVIDENCE_EXPORTER = PASS      DTO_IMMUTABLE          = PASS
EVIDENCE_REDUNDANCY          = PARTIAL
EVIDENCE_SOURCE_INDEPENDENCE = PENDING   EVIDENCE_PRODUCER_TRUST = REQUIRED_PHASE_0
CLAIMED_RESULT_PRODUCT_SOURCE = PASS  (see docs/CLAIMED_RESULT.md)
AEP_INTEGRATION = DEFERRED               AEP_REQUIRED_FOR_PHASE_0 = NO
DURABLE_EVIDENCE_GATE = PENDING          CRASH_RECOVERY_GATE = PENDING
RELEASE_FREEZE = NO
```

`ResourceBindingMetric` precedence:

```
known incompatible scope                     -> UNSUPPORTED
else missing/malformed required evidence     -> INSUFFICIENT_EVIDENCE
else evaluate invariants                     -> PASS | FAIL
```

`CLAIMED_RESULT_PRODUCT_SOURCE=PENDING`: the exporter represents observed facts; a `ClaimedResult`
is a nominal declaration by a gate/orchestrator. The two origins must not be merged without their
own contract (who may declare `RESOURCE_BINDING_GATE=PASS`, with what provenance, and how it reaches
the auditor without becoming evidence of itself). Until then the claim is built in
`tests/audit_support.py` only. `M_EXPORTER_HIDES_REOPEN_CONSISTENT` is deliberately not killed.
