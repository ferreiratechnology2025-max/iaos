# Skill contract - Phase 0

```
IAOS_SKILL_CONTRACT_GATE
SKILL_SCHEMA_CLOSED            = PASS
SKILL_IMMUTABLE                = PASS
SKILL_PROVENANCE               = PASS
SKILL_CAN_INFLUENCE_COGNITION  = YES
SKILL_CAN_CREATE_AUTHORITY     = NO
SKILL_CAN_EXECUTE              = NO
SKILL_CAN_GRANT_CAPABILITY     = NO
SKILL_CAN_BYPASS_GOVERNANCE    = NO
GOVERNANCE_IMPORTS_SKILLS      = NO
RUNTIME_IMPORTS_SKILLS         = NO
ASSURANCE_IMPORTS_SKILLS       = NO
```

## Position
```
Intent -> Skill Resolution -> Skill Context -> Cognition / Model -> Proposal -> Governance -> Execution
```
A Skill enters only above `Proposal`. Skill != agent runtime != tool != capability != authority.

## Contract (`skills/contract.py`, standard library only)
```
SkillDefinition { schema_version(=1) skill_id name domain description
                  mission critical_rules[] workflow[] deliverables[] evaluation_criteria[]
                  provenance { source source_path source_revision } }
```
* CLOSED: unknown or missing keys are rejected (also inside `provenance`); there is deliberately no field for tools,
  permissions, capabilities, credentials, AuthorizedAction, ExecutionEnvelope or hooks. A document carrying one is rejected, not ignored.
* IMMUTABLE: frozen dataclasses, tuple collections, input lists are copied; `to_dict` returns independent copies.
* PROVENANCE: all three fields required and non-blank, including the source revision.
* The only public behaviour is serialization (`to_dict` / `from_dict`): a Skill cannot execute or grant anything.
* Content from `agency-agents` is editorial source for adaptation, never executable code.

## Tests and mutants
`tests/test_skill_contract.py` (120 tests incl. AST import boundary in both directions).
`tests/test_skill_contract_mutants.py`: `M_SKILL_ACCEPTS_UNKNOWN_KEY`, `M_SKILL_MUTABLE`,
`M_SKILL_PROVENANCE_OPTIONAL`, `M_SKILL_ALIASES_INPUT_LIST` - all KILLED by their named test.

## Limits
* The import tests are a lint of intent, not a security boundary.
* Provenance is declared, not verified (no fetch, no signature): `source_revision` is a claim about the source.
* Not in this checkpoint: the pilot (`Minimal Change Engineer`), skill evaluation (with/without skill), the resolver.
