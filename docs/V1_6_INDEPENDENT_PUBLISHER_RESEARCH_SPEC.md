# IAOS V1.6 — Governed Independent Publisher Research

## Status

```text
V1_6_PHASE=SPEC_AND_PREFLIGHT_ONLY

V1_6_CODE_CHANGES=NO
V1_6_LIVE_RUN=NO

V1_6_SPEC_AND_PREFLIGHT_GATE=PENDING
```

O V1.5 é uma dependência congelada e não pode ser alterado para satisfazer este gate.

## 1. Claim

```text
V1_6_CLAIM=GOVERNED_INDEPENDENT_PUBLISHER_RESEARCH
```

Um único AgentLoop, a partir de um único objetivo de alto nível, deve:

1. pesquisar pelo menos dois documentos;
2. obter esses documentos de pelo menos dois publishers independentes;
3. executar cadeias distintas de busca e leitura;
4. admitir pelo menos um fato proveniente de cada publisher;
5. preservar a proveniência independente de cada fato;
6. sintetizar fatos dos dois publishers em um único artefato externo;
7. observar novamente o estado final;
8. passar por verificação determinística;
9. operar sem intervenção humana ou do coder.

## 2. Escopo mínimo

```text
DOCUMENT_COUNT_REQUIRED >= 2
PUBLISHER_COUNT_REQUIRED >= 2
```

O claim não pode ser satisfeito apenas por URLs diferentes:

```text
different URLs
!=
different documents
!=
different publishers
!=
independent sources
```

## 3. Publisher independence

Para o V1.6:

```text
publisher_independence :=
    publisher_identity_A != publisher_identity_B
    AND registered_domain_A != registered_domain_B
    AND editorial_control_A != editorial_control_B
    AND neither document is a mirror, syndication,
        or republication of the other publisher's document
```

A identidade dos publishers deve ser definida e validada no preflight, antes de qualquer execução live. Os publishers não podem ser escolhidos retrospectivamente em função de quais sites o agente consegue operar com mais facilidade.

O V1.6 não afirma independência epistemológica das evidências. Dois publishers independentes podem legitimamente utilizar uma mesma fonte primária.

```text
INDEPENDENT_PUBLISHER_RESEARCH=CLAIM
SOURCE_INDEPENDENCE=NOT_PROVEN
INDEPENDENT_PRIMARY_EVIDENCE=NOT_PROVEN
```

## 4. Provenance contract

As duas cadeias devem ser distintas:

```text
search_execution_A != search_execution_B
read_execution_A   != read_execution_B

document_A != document_B
publisher_A != publisher_B
```

E:

```text
fact_A admitted from read_A
fact_B admitted from read_B

read_A linked to search_A
read_B linked to search_B
```

O uso de um `ResearchBudget` compartilhado é permitido:

```text
SEPARATE_BUDGETS=NOT_REQUIRED
```

Um orçamento compartilhado não pode permitir que um único publisher satisfaça sozinho `PUBLISHER_COUNT_REQUIRED=2`.

## 5. Artifact contract

O artefato final deve depender materialmente dos fatos provenientes de ambos os publishers. O verifier deve provar:

```text
CHAIN_A_VALID=YES
CHAIN_B_VALID=YES

PUBLISHERS_DISTINCT=YES

ARTIFACT_CONTAINS_FACT_A=YES
ARTIFACT_CONTAINS_FACT_B=YES
```

Também deve existir teste contrafactual:

```text
REMOVE_FACT_A => VERIFIER_FAIL
REMOVE_FACT_B => VERIFIER_FAIL
```

Assim, o segundo publisher não pode ser apenas decorativo. A provenance pode permanecer no sistema de Assurance; o artefato não precisa necessariamente exibir URLs ou nomes de publishers, salvo se o próprio goal exigir isso.

## 6. Assurance

O gate final deverá preservar:

```text
fresh final observation
deterministic verification
separate provenance chains
dual-publisher contribution
```

O LLM não decide sozinho se o resultado é verdadeiro ou completo.

```text
Claim != Evidence
Evidence != Audit
Audit != Authority
```

## 7. Out of scope

O V1.6 não tenta provar:

```text
REPEATABILITY
TRANSACTIONAL_EFFECTS
IDENTITY_DELEGATION
MULTI_AGENT
MODEL_ROUTING

SOURCE_INDEPENDENCE
INDEPENDENT_PRIMARY_EVIDENCE

GENERAL_TOOL_ROUTING
DYNAMIC_CAPABILITY_DISCOVERY
UNIVERSAL_TOOL_ACCESS
```

## 8. SPEC_AND_PREFLIGHT gate

Nenhuma implementação ou run live é permitida até:

```text
V1_6_SPEC_AND_PREFLIGHT_GATE=PASS
```

O gate exige:

```text
PUBLISHER_A_IDENTITY=KNOWN
PUBLISHER_B_IDENTITY=KNOWN

REGISTERED_DOMAINS_DISTINCT=YES
EDITORIAL_CONTROL_DISTINCT=YES
MIRROR_OR_SYNDICATION_RELATIONSHIP=NO

DOCUMENT_A_CANONICAL=KNOWN
DOCUMENT_B_CANONICAL=KNOWN
DOCUMENTS_DISTINCT=YES

FACT_A=DETERMINISTICALLY_EXTRACTABLE
FACT_B=DETERMINISTICALLY_EXTRACTABLE

SEARCH_CHAIN_A_PREFLIGHT=PASS
READ_CHAIN_A_PREFLIGHT=PASS

SEARCH_CHAIN_B_PREFLIGHT=PASS
READ_CHAIN_B_PREFLIGHT=PASS

ONE_HIGH_LEVEL_GOAL=DRAFTED

VERIFIER_DESIGN=PASS

NO_POST_HOC_PUBLISHER_SELECTION=YES
```

## 9. Ordem obrigatória da fase

```text
select publisher pair
→ prove publisher independence
→ select canonical documents
→ select deterministic fact A and fact B
→ draft one high-level goal
→ design deterministic verifier
→ execute read-only preflight
→ evaluate V1_6_SPEC_AND_PREFLIGHT_GATE
```

Somente após `V1_6_SPEC_AND_PREFLIGHT_GATE=PASS` pode ser considerada uma nova autorização para `V1_6_CODE_CHANGES` ou `V1_6_LIVE_RUN`. Nenhuma dessas autorizações está implícita pelo PASS do preflight.

## 10. Gate documental

```text
V1_6_DOCUMENTATION_GATE

README_UPDATED=YES
V1_5_FREEZE_STATE_DOCUMENTED=YES
V1_6_CLAIM_DOCUMENTED=YES
PUBLISHER_INDEPENDENCE_CONTRACT_DOCUMENTED=YES
PROVENANCE_CONTRACT_DOCUMENTED=YES
VERIFIER_CONTRACT_DOCUMENTED=YES
OUT_OF_SCOPE_DOCUMENTED=YES
SPEC_AND_PREFLIGHT_GATE_DOCUMENTED=YES

CODE_DIFF_OUTSIDE_DOCS=0
V1_5_FREEZE_RECORD_CHANGED=NO
```
