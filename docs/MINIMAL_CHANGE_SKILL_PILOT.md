# Minimal Change Engineer - extraction and evaluation-harness fixture

```
(superseded framing) original question: does this Skill measurably improve cognition without acquiring any power?
UPSTREAM_REPO      = msitarzewski/agency-agents     (MIT)
UPSTREAM_REVISION  = 83294689da3832c0a9f223221148c411fd3eacc0
UPSTREAM_PATH      = engineering/engineering-minimal-change-engineer.md
UPSTREAM_BLOB      = 11d7660010fb70c5fcd4c36e2239afe357ecd335   (re-verified from the stored snapshot)
UPSTREAM_ACCESS    = READ_ONLY   INSTALL_SCRIPTS = NOT RUN   BULK_IMPORT = NO   AGENT_RUNTIME = NO
RESOLVER = OUT_OF_SCOPE   CATALOG = OUT_OF_SCOPE
```

## Corrected state (design review after the run)
```
IAOS_SKILL_CONTRACT_GATE                    = PASS
MINIMAL_CHANGE_EXTRACTION                   = PASS
MINIMAL_CHANGE_EVAL_HARNESS                 = PASS
MINIMAL_CHANGE_CAPABILITY_ACQUISITION_TEST  = NOT_VALID_AS_DESIGNED
  REASON                                    = baseline already possessed most of the tested behavior
SKILL_ARCHITECTURE_VALIDATION               = PASS
SKILL_TASK_CAPABILITY_VALIDATION            = PENDING
```
A Skill is loadable procedural knowledge (what to do, how, in what order, what to deliver, how to know it is done).
The question for it is therefore not "does the model produce a smaller diff", but "does the model, given the Skill, perform
a procedure it could not reliably improvise, and produce that Skill's own deliverables?" - judged by an evaluator derived
from the Skill's deliverables, not from a general style metric. The with/without run below measured model discipline, so its
PASS is kept as a record of the harness working, not as evidence of capability acquisition.

## Pipeline
```
upstream source (pinned, verbatim, read-only)   skills/sources/agency-agents/<revision>/
        -> manual adaptation + trace             skills/definitions/minimal-change-engineer.{json,trace.json}
        -> SkillDefinition (closed contract)     skills/contract.py
        -> Skill Context text for cognition      skills/context.py   (pure function, no provenance, no authority)
        -> evaluation with / without the skill   skilleval/
```
SOURCE_TEXT != INSTALLED_SKILL: the verbatim file exists only for traceability and hash re-verification.
Dropped from the source: front matter, persona/identity/memory narrative, communication style, PR/issue/review behavior,
the "delete and see what breaks" technique, advanced capabilities.

## Part 1 - extraction fidelity
```
SOURCE_PINNED                       = PASS   snapshot hashes to the pinned git blob
PROVENANCE_EXACT                    = PASS   40-hex revision, never a branch
ADAPTATION_TRACEABLE_TO_SOURCE      = PASS   every item has verbatim anchors found in the source
NO_TOOL_AUTHORITY_IMPORTED          = PASS   closed schema + text lint (no tools/permissions/capabilities/credentials ...)
NO_RUNTIME_SEMANTICS_IMPORTED       = PASS   no persona, memory, PR/issue/commit/install behavior in the adapted text
```
The text checks are lint, not proof of absence of intent.

## Part 2 - measured value
Same 12 tasks, two arms: CONTROL (base instructions) and TREATMENT (identical base instructions + the rendered skill context).
Small fixture repositories with deliberate temptations (smelly neighbours, a sibling with the same latent bug, a second constant
with the same value, a stricter error path that must keep raising). The model edits a throw-away copy; it has no Governance,
runtime or evidence path. Run ids are random; hidden tests and the scorer sit outside the run directory.

Per run (deterministic, `skilleval/scoring.py`): `requirements_satisfied`, `files_touched`, `lines_changed`,
`scope_creep_events` (distinct changed (file, owner) outside the task's allowed set, by AST), `unrequested_refactors`,
`regressions` (hidden `test_reg_*` + visible tests; visible tests are restored from the task, so they cannot be weakened).

Pre-registered rule (`skilleval/gate.py`, written before the runs): requirements not lower -> regressions not higher ->
control must show scope creep (else INCONCLUSIVE) -> scope creep strictly lower -> total diff not larger.
The smallest diff never outranks correctness.

### Result (recorded in `skilleval/pilot_minimal_change/`, re-scored from stored final states by a test)
| | control | treatment |
|---|---|---|
| tasks fully satisfied | 12/12 | 12/12 |
| regressions | 0 | 0 |
| files touched | 12 | 12 |
| lines changed | 41 | 37 |
| scope-creep events | 1 | 0 |

`HISTORICAL_RULE_VERDICT=PASS` (a record of the harness, not an efficacy gate), `EVIDENCE_STRENGTH=LOW`. The only difference is one task (t12-tax-rounding): control rewrote the
rounding with `Decimal` (new import, 6 lines) where treatment changed one expression (2 lines). On the other 11 tasks both arms produced the same minimal change.

## Limits (explicit)
* The control arm was already near-minimal: little headroom, so the rule is satisfied by one event in one task.
* One sample per task and arm: no variance estimate; one model family; small synthetic Python repositories.
* Scope creep is structural; it does not judge whether an out-of-scope change was good.
* Not demonstrated: effect on harder or longer tasks, other languages, or other models.
* Resolver and catalog are not built; the Skill is never selected automatically.
