# Authorization correspondence - Phase 0

```
IAOS_AUTHORIZATION_CORRESPONDENCE_GATE   question: what reached the execution is EXACTLY what was authorized?
METRIC                       = AuthorizationCorrespondenceMetric (gate id AUTHORIZATION_CORRESPONDENCE)
AUTHORIZES_ANYTHING          = NO      CONSULTS_GOVERNANCE = NO      READS_CLAIM = NO
EVIDENCE_SOURCE_INDEPENDENCE = PENDING
EVIDENCE_PRODUCER_TRUST      = REQUIRED_PHASE_0
RELEASE_FREEZE               = NO
```

## Two different properties
```
ResourceBindingMetric             the resource really used is the authorized resource      (effect side)
AuthorizationCorrespondenceMetric the authority executed equals the authority granted      (authority side)
```
`governance.envelope.authorizes_static` is the pre-effect CONTROL (pure, stops a divergence before consuming).
This metric is the independent AUDIT of the same property from observed facts: it does not call, import or
reuse that predicate (AST-tested). If the control fails or is bypassed, this is the second line.

## Evidence (two sides, plain immutable DTOs in `evidence/types.py`)
```
AuthorizedAuthority   authorization_id intent_id opcode authorized_args resource_binding_id authorization_digest
ExecutedAuthority     authorization_id intent_id opcode executed_args   resource_binding_id authorization_digest
AuthorizationCorrespondenceEvidence(schema_version, execution_id, authorized, executed)
```
* authorized side: exported from the immutable `AuthorizationRecord` Governance issued (its own `authority_digest()`).
* executed side: `result.observation.executed`, an `ExecutedAuthorityObservation` recorded by `AEPRuntime` when the
  validated envelope reached the effect boundary (`opcode` = the executor opcode that actually ran, `executed_args` =
  the args handed to the executor). The runtime never reads it back; it is an observation, not an input to any decision.
* neither side is derived from the other, from a claim or from a default; absence stays `None`.

## Verdict (same precedence as ResourceBindingMetric)
```
known incompatible scope (schema_version present and != 1)   -> UNSUPPORTED
else any field missing / malformed on either side              -> INSUFFICIENT_EVIDENCE   (None never matches None)
else compare the six fields exactly                            -> PASS | FAIL
```
PASS needs exact equality of `authorization_id`, `intent_id`, `opcode`, `args` (canonical, ordered: widening, narrowing
and reordering are all FAIL), `resource_binding_id` and `authorization_digest`. Every divergence is listed in `violations`
(`authorization_id_mismatch intent_mismatch opcode_mismatch args_mismatch binding_mismatch digest_mismatch`).
`resource_binding_id` is required: authority without a binding is outside what this Phase 0 metric can attest.
`claimed PASS` with verified != PASS keeps `silent_escape=true` (auditor, unchanged).

## Composition
`assurance.chain.AuthorizationCorrespondenceChain` owns the expected gate; claim (`claims.producer`) and evidence
(`evidence.exporter`) are produced separately; the claim only correlates the subject and feeds `silent_escape`.
`audit.auditor.default_metrics()` now holds both metrics; the expected gate (never the claim) selects which runs.

## Tests
`tests/test_authorization_correspondence.py`: exact PASS, per-field divergence FAIL, widened/narrowed/reordered args,
missing/malformed INSUFFICIENT (one side and both sides), UNSUPPORTED, claim handling, subject correlation, DTO, structure,
and real-runtime runs:

* GOOD: authorization -> real execution -> `PASS`; the executed side is observed by the runtime, not copied.
* `PRODUCT_RUNTIME_DRIFT_REJECTION=PASS` (line 1): the product runtime REFUSES a drifted envelope (intent / digest
  drift) before consuming and before any effect.
* `AUDIT_DEFENSE_IN_DEPTH_BAD=PASS` (line 2): if a defective runtime or bypass lets it through (a test double that
  skips `authorizes_static`; not the product runtime), the metric returns `FAIL` and the runtime's `claimed PASS`
  becomes `silent_escape`.
* Evidence altered after a real run: opcode, args, binding, intent, authorization id, digest -> `FAIL`.
* executed facts of a different real authorization -> `FAIL`; no observation at all -> `INSUFFICIENT_EVIDENCE`.

Opcode / args / binding drift cannot be driven through the real runtime (it validates them before executing), so those
are exercised by altering the evidence of a real run; this is stated, not hidden.

## "Executed" (precise definition)
`ExecutedAuthorityObservation` != consumed authorization, != validated envelope, != handoff attempted.
It means: the effect handler actually entered execution. It is published only after the handler is entered.
```
EXEC_OBSERVATION_AT_EFFECT_BOUNDARY       = PASS
OBSERVATION_ON_STATIC_REJECT              = NO    test_c_exec_observation_not_emitted_on_static_reject
OBSERVATION_ON_HANDOFF_FAILURE            = NO    test_c_exec_observation_not_emitted_on_handoff_failure (consume happened, handler never entered)
OBSERVATION_ON_EFFECT_FAILURE_AFTER_ENTRY = YES   test_c_exec_observation_exists_on_effect_failure
```
`test_c_exec_observation_not_published_before_handler_entry` kills `M_EXEC_OBSERVED_BEFORE_HANDLER`.
Known limit: when the effect fails after entry the observation lives in `runtime.observations()`, not in
`result.observation` (the exception is swallowed); the exporter then yields INSUFFICIENT_EVIDENCE while the claim is FAIL,
so there is no silent escape. Not changed in this checkpoint.

### Mutants (`tests/test_correspondence_mutants.py`, isolated copy, each killed by a named test)
| mutant | killed by |
|---|---|
| `M_OPCODE_DRIFT` | `test_opcode_drift_is_fail` |
| `M_ARGS_WIDENED` | `test_args_widened_or_changed_is_fail` |
| `M_BINDING_CHANGED` | `test_binding_changed_is_fail` |
| `M_INTENT_CHANGED` | `test_intent_changed_is_fail` |
| `M_AUTHORIZATION_ID_CHANGED` | `test_authorization_id_changed_is_fail` |
| `M_DIGEST_IGNORED` | `test_digest_difference_is_fail` |
| `M_MISSING_FIELD_AS_MATCH` | `test_same_field_missing_on_both_sides_is_insufficient_never_a_match` |
| `M_CLAIM_USED_TO_FILL_EXECUTION_EVIDENCE` | `test_missing_executed_side_is_insufficient_not_filled_from_claim_or_authorized` |
| `M_EXPORTER_FILLS_EXECUTED_FROM_AUTHORIZED` | `test_real_missing_observation_is_insufficient_with_silent_escape` |
| `M_EXEC_OBSERVED_BEFORE_HANDLER` | `test_c_exec_observation_not_published_before_handler_entry` |

All 10 mutants KILLED.

## Limits (explicit)
* A producer that lies consistently on BOTH sides (authorized and executed) is not detected: `EVIDENCE_SOURCE_INDEPENDENCE=PENDING`.
* The digest is compared as an opaque value (hash != signature); it is not recomputed by the auditor, which keeps the
  audit free of governance's canonicalization.
* Authority with no `resource_binding_id` is INSUFFICIENT_EVIDENCE (Phase 0 attests binding-bearing `fs.read` authority only).
* The authorized side is read from the in-process authorization record; there is no cryptographic authenticity of it.
