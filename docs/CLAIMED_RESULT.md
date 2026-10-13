# ClaimedResult product contract (IAOS_CLAIMED_RESULT_GATE)

```
CLAIM            = what a component nominally asserts
EVIDENCE         = what was observed
AUDIT            = whether the evidence supports the claim
ClaimedResult != Evidence      ClaimedResult != Authorization      AuditReport != Authorization
CLAIM_NON_EVIDENTIARY   CLAIM_NON_AUTHORITY
```

## Roles
- **Claimant**: the component whose execution is declared. For `RESOURCE_BINDING` in Phase 0 it is
  `IAOS_GOVERNED_EXECUTION_RUNTIME`.
- **ClaimProducer** (`claims/producer.py`): only materializes the nominal outcome into the schema. It is
  not the semantic source of the assertion. It imports nothing but `claims.types` and never receives
  evidence, an audit report, a metric result, the exporter or the auditor.

## ClaimedResult (`claims/types.py`)
Closed, immutable: `schema_version, claim_id, gate_id, claimed_status, execution_id, authorization_id,
claimant_component, provenance{source_kind, source_id}`. `claimed_status` is `PASS | FAIL | INCONCLUSIVE`
(`UNSUPPORTED` / `INSUFFICIENT_EVIDENCE` belong to the auditor and are rejected). It carries no resource
identity, counters, binding id, platform, filesystem, metric or audit result.
Provenance means only "this declaration says it came from this origin" (`EXECUTION_RESULT`, the
execution id); it does not mean trusted, verified, true or authorized. No signature/MAC in Phase 0: a
false claim grants no power, it is what the auditor confronts.

Nominal rule (`RESOURCE_BINDING`): execution ok -> PASS, failed -> FAIL, not reported -> INCONCLUSIVE.
A claim that cannot be formed (no execution id) raises instead of inventing one.

## Chain
```
runtime result -> ClaimProducer -> ClaimedResult  --\
                                                      >-> Auditor -> AuditReport
runtime observables -> EvidenceExporter -> DTO     --/
```
`assurance/chain.py` is the composition level (the only code importing producer, exporter and auditor).
Nothing in `governance/`, `audit/`, `evidence/` imports `claims.producer`; `claims/` imports none of
audit/evidence/governance; `audit` may import only the neutral `claims.types`.

## Auditor rules
`CLAIM_DOES_NOT_SELECT_VERIFICATION_POLICY`: the product (`AssuranceChain`, via the auditor's `expected_gate_id`)
decides which metric judges a claim. `claim.gate_id` is only compared with it: on mismatch the metric does
not run, the report carries `CLAIM_GATE_MISMATCH` (`INSUFFICIENT_EVIDENCE`, never PASS) and a nominal PASS is a
`silent_escape`. An unknown gate has no fallback metric. The runtime-facing producers
(`produce_resource_binding_claim`, `claim_from_execution_result`) have a fixed gate and no `gate_id` parameter.

Otherwise the claim is used for (1) subject correlation (`claim.execution_id == evidence.execution_id`, same for
`authorization_id`) and (2) computing `silent_escape` after evaluation. It is never an input to a metric and
never completes evidence (`_evaluate_metric` and `_select_metric` ignore it; AST-tested). A present evidence
subject that differs from the claim's gives `INSUFFICIENT_EVIDENCE` with `SUBJECT_MISMATCH`, and the metric does
not run on that evidence. `silent_escape = claimed PASS and verified != PASS` (FAIL/INCONCLUSIVE claims never are).

Mutants: `M_CLAIM_SELECTS_METRIC` (killed by C10), `M_UNKNOWN_GATE_FALLBACK` (killed by C11).
`M_EXPORTER_DEFAULTS_MISSING_AS_SAFE` is also killed in a case where subject correlation passes, so the kill
comes from the falsified evidence and not incidentally from `SUBJECT_MISMATCH`.
## Status
`CLAIMED_RESULT_PRODUCT_SOURCE=PASS` (RESOURCE_BINDING). Still `EVIDENCE_SOURCE_INDEPENDENCE=PENDING` and
`EVIDENCE_PRODUCER_TRUST=REQUIRED_PHASE_0`: the claim and the evidence both originate in the runtime's own
result, so a runtime that lies consistently in both is not detected.
