# Incident Response Skill — epistemically framed run (no Skill vs new-framed Skill)

```text
IAOS_SKILL_EPISTEMIC_FRAMING_REPLICATION            = NOT_EXECUTED_AS_PAIRED_OLD_VS_NEW
IAOS_INCIDENT_RESPONSE_CAPABILITY_WITH_NEW_FRAMING  = FAIL   (pre-registered rule: "treatment invents more facts")
PROCEDURAL_CAPABILITY_ACQUISITION                   = STRONG (procedure 13 -> 43/50, ordering 12 -> 19/20, concrete 11 -> 20/20, total 45 -> 91/100)
EPISTEMIC_GROUNDING                                 = FAIL   (invented facts 3.0 -> 9.5)
INCIDENT_SKILL_PRODUCT_ACCEPTANCE                   = NOT_YET
```

## What was run
CONTROL = no Skill; TREATMENT = Skill rendered with the epistemic framing (`skills/context.py`, commit cf8c1ac).
10 scenarios x 2 arms = 20 responses, 2 blind judges each = 40 judgements, frozen prompt set (`framing.py`, sha256 399d7ca8...),
frozen rubric and scorer, interleaved blinded order. It is NOT an OLD-framing vs NEW-framing comparison: no old-framing
condition was generated in this setup. The older baseline (`../runs.json`, FAIL, invented 14.0 vs 2.5) came from a separate run.

Files: `preregistration.json` (written before the first model call), `blind_plan_DEBLINDED.json` (sha256 matches
`plan_sha256` in the pre-registration), `runs.json`, `results.json`, `epistemic_error_analysis.json`.
Served model: UNVERIFIABLE. Sampling controls: not observable. Responder and judges are the same model family.

## Causal tracing of the 9.5 invented facts (treatment; no Skill/rubric/scorer change)
| class | weight |
|---|---|
| A skill procedure attributed as organization policy | 0 |
| B numeric/default threshold invented from the skill | 0 |
| C role/process existence assumed | 0 |
| D system/environment fact invented | 9.5 |
| E unrelated hallucination | 0 |

Skill anchors of the 9.5: 4.0 from the blameless rule ("say the system allowed the failure mode") -> the model writes
"the system allowed X with no staged rollout / no early alert" as an established fact; 2.0 from the review's "contributing
factors" deliverable -> contributing factors asserted before evidence; 1.0 from the mitigation step ("revert...") -> a "tested"
restore path; 2.5 with no Skill anchor. Control's 3.0 has no Skill anchor.

Descriptive only (different samples, not paired): policy-attribution claims in the treatment arm were 7.0 of 14.0 with the old
context and 0 of 9.5 with the framed context. The framing appears to have removed that class; the remaining defect is
Skill templates (blameless, contributing factors) being instantiated as claims about the environment.
Classification A-E and anchors were assigned manually after reading each excerpt; extraction of the quotes is deterministic.

## Limits
n=10 scenarios, 1 sample per cell, same-family judges, one model family; analysis labels are a single reader's judgement.
