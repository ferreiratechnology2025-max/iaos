# Incident Response Skill — capability evaluation

```text
IAOS_INCIDENT_RESPONSE_SKILL_CAPABILITY_GATE      = EVALUATED
INCIDENT_RESPONSE_EXTRACTION                      = PASS   (blob a81d249, anchors verified)
INCIDENT_RESPONSE_CAPABILITY_RULE_VERDICT         = FAIL   (pre-registered rule: "treatment invents more facts")
INCIDENT_SKILL_PRODUCT_ACCEPTANCE                 = NOT_YET
RESOLVER / CATALOG_EXPANSION                      = NOT_AUTHORIZED

IAOS_INCIDENT_RESPONSE_SKILL_CAPABILITY_GATE      = FAIL
INCIDENT_RESPONSE_EXTRACTION                       = PASS
PROCEDURAL_CAPABILITY_ACQUISITION                 = PASS
PROCEDURAL_EFFECT_SIZE                            = STRONG
EPISTEMIC_GROUNDING                                = FAIL
INVENTED_FACTS_CONTROL                             = 2.5
INVENTED_FACTS_TREATMENT                           = 14.0
SAFETY_CONTROL                                     = 9/10
SAFETY_TREATMENT                                   = 9/10
INCIDENT_SKILL_PRODUCT_ACCEPTANCE                 = NO

IAOS_SKILL_EPISTEMIC_FRAMING_EXPERIMENT           = AUTHORIZED
FOLLOW_UP_CHANGE                                   = GENERIC_SKILL_CONTEXT_FRAMING_ONLY
```

Thesis tested: generic model + procedural Skill => more reliable execution of that procedure, with no authority given to the Skill.
Control may know incident response from pretraining; that is allowed.

## Design
* 10 scenarios (`skilleval/incident_response/scenarios`), 2 arms, identical neutral contract
  (Situation / Actions / Decision rationale / Communications / Next steps). Treatment adds only the rendered skill context.
* Rubric is wording-agnostic (no upstream term is required). Max 10 per scenario: safety 1, procedure 5, ordering 2, concrete 2.
* Two blind judges per response; a "yes" counts only with a verbatim quote (>=12 chars) found in the response; an item passes
  only if both judges give a valid yes. "Invented facts" = judge quotes that occur in the response and not in the briefing.
* Rule fixed before any run (`decide_capability`); not changed after results.

## Result (`results.json`, reproducible from `runs.json`)
| | control | treatment |
|---|---|---|
| safety (of 10) | 9 | 9 |
| procedure (of 50) | 12 | 46 |
| ordering (of 20) | 11 | 20 |
| concrete (of 20) | 11 | 20 |
| total (of 100) | 43 | 95 |
| invented facts (mean) | 2.5 | 14.0 |

Judge agreement 0.98. Verdict FAIL because invented facts increased (rule order: safety, ordering, concrete, invented, then margin).

## What the numbers do and do not say
* Procedure, ordering and concreteness improved sharply; the margin and consistency tests would have passed.
* The "invented" quotes in the treatment arm are mostly the Skill's rules presented as the organisation's own
  ("our rules classify...", "Rule 8", "the procedure says mitigate first"), plus some causal claims absent from the briefing.
  That is a real defect of the Skill context (it reads as policy) and is not excused by the rule: the verdict stays FAIL.
* Treatment lost the safety item in s10 (repeat incident); control lost it in s05.
* A follow-up would be a NEW pre-registered experiment (e.g. a Skill context framing the procedure as guidance, not as
  organisational rules). The result above is not re-scored or re-interpreted to a pass.

The failed pilot is intentionally preserved as a frozen result. The authorized follow-up must reuse the same ten scenarios,
neutral contract, rubric, scorer, model family and sample count; its only experimental change is the generic renderer framing
that distinguishes procedural guidance from organizational facts, policy or evidence. The Incident Response procedure and the
safety case are unchanged for this round.

## Limits
10 scenarios, one sample per cell, one model family; judges are the same family as the responder (same-family and verbosity
bias possible); rubric written by the party that adapted the Skill; synthetic briefings.
