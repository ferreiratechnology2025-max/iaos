# SPEC → CURRENT IAOS (2026-10)

**Status:** mapa corrente de estado e roadmap. O SPEC normativo não é reescrito por este documento. Esta atualização documental não autoriza implementação de Delivery, Computer Runtime ou integração OpenBot, nem altera Governance.

```ini
IAOS_SPEC_CURRENT_STATE_REFRESH=AUTHORIZED
PHASE_1_IMPLEMENTATION=HISTORICAL_CHECKPOINT
CODE_CHANGED=NO
GOVERNANCE_CHANGES=NO
RUNTIME_CHANGES=NO
NEW_FEATURES=NO
NORMATIVE_SPEC_REWRITTEN=NO
```

## Escopo e leitura

Este arquivo substitui o mapa de gaps anterior como fotografia operacional do IAOS. O histórico Git preserva a fotografia anterior; não há uma segunda fonte de verdade normativa. O overlay do **SPEC v1.1 — Phase 0 Amendments** permanece válido: `recorded` não é `persisted` nem `durable/recoverable`, a correspondência de autoridade continua exata no escopo demonstrado, e os gates de AEP literal, evidence durável, crash recovery e replay cross-process continuam separados.

## Âncora de estado

```makefile
CODE_STATE_THROUGH=939136a
DOCUMENT_STATE_AT=e38f346
```

`CODE_STATE_THROUGH` é o último estado funcional necessário para sustentar a matriz; não significa que seja o último commit do repositório. `DOCUMENT_STATE_AT` identifica o commit documental que esta fotografia está regularizando. Os dois identificadores são deliberadamente distintos.

## Matriz SPEC → CURRENT IAOS

| Área | Estado atual | Evidência / observação | Classificação |
|---|---|---|---|
| Governance | Núcleo Phase 0 implementado e congelado no escopo demonstrado | `governance/`, `tests/adversarial/`, `docs/EXECUTION_ENVELOPE_CONTRACT.md` | **IMPLEMENTED / FROZEN** |
| ExecutionEnvelope / authority correspondence | Correspondência exata, envelope fechado e consumo single-runtime | `governance/aep_adapter.py`, `aep_runtime.py`, testes de envelope/correspondence | **IMPLEMENTED / FROZEN** |
| Resource binding | Open-first e binding de identidade provados para Windows/NTFS/`fs.read` | `governance/handle_binding.py`, testes de resource binding | **FROZEN_REAL no escopo** |
| Agent | `DailyBriefAgent` e contrato mínimo existem | `cognitive/agent.py`, `tests/test_phase1a_contracts.py` | **IMPLEMENTED_MINIMUM** |
| Capability | Contrato e capabilities reais de produto existem | `cognitive/contracts.py`, `email.read`, `calendar.read`, `web.search` | **IMPLEMENTED** |
| Tool | Implementações e contratos de tool existem | `cognitive/tools.py`, executors external-read | **IMPLEMENTED** |
| Tool Registry | Registry fechado com resolução/fail-closed | `cognitive/registry.py`, `tests/test_phase1a_contracts.py` | **IMPLEMENTED** |
| Assistant | Assistant default composto com o Daily Brief agent | `cognitive/agent.py` | **IMPLEMENTED_MINIMUM** |
| LLMProvider | Abstração e adapter Agnes existem | `cognitive/provider.py`, `cognitive/agnes.py` | **IMPLEMENTED** |
| Proposal / Workflow | Contratos estruturados de produto existem | `cognitive/contracts.py`, planner/workflow tests | **IMPLEMENTED** |
| Planner | Planner determinístico do Daily Brief existe | `cognitive/planner.py` | **IMPLEMENTED** |
| `calendar.read` | Google Calendar real, governado e provado | `application/google_*`, external-read tests | **FROZEN_REAL** |
| `email.read` | Gmail real, governado e provado | `application/google_*`, bounded-read contract/tests | **FROZEN_REAL** |
| `web.search` | Brave real, governado e provado | `application/scouts.py`, external-read tests | **FROZEN_REAL** |
| Daily Brief | UI/API + execução real end-to-end | `application/daily_brief*.py`, integration/UI docs | **FROZEN_REAL** |
| History | Persistência de runs e source refs | `application/daily_brief_history.py` | **FROZEN** |
| Routine | Schedule persistente do Daily Brief | `application/daily_brief_routine.py` | **FROZEN** |
| Scouts V1A | Scouts read-only persistentes com schedule/history | `application/scouts.py`, `tests/test_scouts.py` | **FROZEN_REAL** |
| Scouts V1B | Findings consumidos pelo Daily Brief | `tests/test_scouts_v1b.py`, Daily Brief result/history | **FROZEN_REAL** |
| Memory / `MemoryRecord` | Não implementado | Ausência é intencional; Daily Brief provou valor sem memória | **MISSING/DEFERRED** |
| Learner | Não implementado | Sem candidate/feedback loop | **MISSING/DEFERRED** |
| Learning Governance | Não implementado | Sem aprovação de artefatos aprendidos | **MISSING/DEFERRED** |
| Assistant Federation | Não implementado | Sem registry/isolation multi-Assistant | **MISSING/DEFERRED** |
| Generic durable Session | Runs especializados existem; Session genérica não | `DailyBriefRun`/`ScoutRun` não substituem Session | **MISSING** |
| Dynamic Capability Discovery | Não há resolver cognitivo dinâmico | Capabilities conhecidas são resolvidas por registry | **MISSING** |
| Durable Evidence | Store ainda é in-memory | Persistência/reconstrução após restart não provadas | **DEFERRED** |
| Crash Recovery | Não implementado | Recovery protocol e restart-safe consumption pendentes | **DEFERRED** |
| Cross-process exactly-once | Ledger local ao processo | Replay cross-process ainda pendente | **DEFERRED** |
| Evidence Source Independence | Observação ainda vem do runtime próprio | Auditoria estrutural existe; independência é pendente | **PENDING** |
| AEP literal integration | Deliberadamente não conectada | Boundary de efeito é do IAOS local | **DEFERRED** |
| Production Release Freeze | Release não congelado | `RELEASE_FREEZE=NO` permanece correto | **NO** |
| External writes / Delivery | Nenhum efeito externo implementado | Delivery é apenas o próximo slice autorizado | **NOT_STARTED** |

## Checkpoint congelado

```ini
DAILY_BRIEF_CAPABILITY_V1=FROZEN_REAL
ROME_EXTRACTION_1=COMPLETE
LEGACY_SCOUT_FINDINGS_WITHOUT_ID=KNOWN_NON_BLOCKING_DEBT
LEGACY_FINDINGS_MIGRATION=NO
```

O Daily Brief é hoje a primeira capability composta: connectors reais → History → Routine → Scouts → findings consumidos pelo brief. Isso é capability formation comprovada, sem justificar ainda um framework genérico.

## Rome Extraction 1

**BORROWED:** durable product capability; Run Now; History; Routine; Scouts; consumed/unconsumed findings; capability composition; purpose-built UI.

**NOT_BORROWED:** Rome security model; generic App framework; generic Routine engine; self-evolution; marketplace/App Store; memory architecture.

Rome permanece referência de produto, persistência e composição. A arquitetura de autoridade do IAOS permanece própria: `Capability ≠ Authority`, `Intent ≠ Authorization`, `Tool ≠ Permission`, `Agent ≠ Executor`, `Claim ≠ Evidence`, `Evidence ≠ Assurance`.

## Decisão explícita de roadmap: Governed Computer primeiro

```ini
NEXT_EXPERIMENT=OPENBOT_COMPUTER_SPIKE
OPENBOT_COMPUTER_SPIKE_STATUS=PARTIAL
SCHEDULED_DELIVERY_V1=DEFERRED
OPENBOT_INTEGRATION_AUTHORIZED=NO
```

Rationale: o vertical read-only do Daily Brief foi provado end-to-end, mas o gargalo observado foi o operator tax. O experimento do computador governado testa uma capacidade horizontal de execução antes de adicionar outro efeito externo.

O princípio é: usar a capacidade determinística mais forte disponível — native capability, API, MCP ou CLI — e recorrer a Computer apenas como fallback governado. `REACHABILITY != AUTHORITY`: abrir uma interface nunca autoriza clicar, enviar, publicar, excluir, comprar ou conceder acesso.

O spike isolado já provou computador, browser/profile, workspace, shell e persistência básica sem alterar o IAOS. O takeover humano, a sessão Google, a auditoria end-to-end e a navegação read-only no Cloud Console permanecem não testados por dependerem do stack completo do OpenBot.

Esta decisão **não autoriza** `email.send`, WhatsApp, `generic external.write`, `communication.send`, mudanças de connector, integração OpenBot ou mudanças de Governance.

## Dívida real preservada

- Durable Evidence, Crash Recovery e Cross-process exactly-once continuam release debt.
- Evidence Source Independence continua pending.
- AEP literal integration continua deferred.
- Production Release Freeze continua `NO`.
- `LEGACY_SCOUT_FINDINGS_WITHOUT_ID` é dívida conhecida não bloqueante; não há migração autorizada.

## Current phase

```makefile
IAOS_CURRENT_PHASE:

FOUNDATION_PROVEN=YES
FIRST_REAL_CAPABILITY_PROVEN=YES
READ_ONLY_PRODUCT_VERTICAL_PROVEN=YES

NEXT_QUESTION:
Which useful external effect should receive narrowly scoped authority?
```

## Gate documental

```makefile
SPEC_CURRENT_STATE_REFRESH_GATE

STALE_MISSING_CLASSIFICATIONS_REMOVED=PASS
DAILY_BRIEF_REAL_STATE_RECORDED=PASS
SCOUTS_V1A_V1B_RECORDED=PASS
ROME_EXTRACTION_1_RECORDED=PASS
REAL_RELEASE_DEBT_PRESERVED=PASS
GOVERNED_COMPUTER_ROADMAP_DECISION_RECORDED=PASS
DELIVERY_V1_DEFERRED=PASS
DELIVERY_EFFECT_IMPLEMENTATION_AUTHORIZED=NO
OPENBOT_INTEGRATION_AUTHORIZED=NO
NORMATIVE_SPEC_REWRITTEN=NO
CODE_CHANGED=NO
```

## Phase 0 amendment overlay

O overlay normativo anterior permanece aplicável: boundary comportamental, correspondência exata, open-first binding e evidence `recorded-before-handoff` estão completos no escopo demonstrado; `DURABLE_EVIDENCE_GATE`, `CRASH_RECOVERY_GATE`, `CROSS_ADAPTER_REPLAY_GATE` e `AEP_KERNEL_INTEGRATION_GATE` continuam pending/deferred. Não converter os gates locais em production readiness.
