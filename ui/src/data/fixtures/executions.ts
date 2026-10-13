import type { ExecutionViewModel, StageVM } from '../../contracts/viewmodels';

/**
 * FIXTURE DATA ONLY. These view models are authored by hand to stand in for
 * what an IAOS adapter would produce from ClaimedResult + AuditReport. The UI
 * must render them as-is: no state or label is derived from raw evidence.
 */

const SCOPE = 'Windows / NTFS / fs.read';
const CLAIMANT = 'IAOS_GOVERNED_EXECUTION_RUNTIME';

const stages = (
  intent: string,
  auth: string,
  exec: string,
  evidence: [StageVM['state'], string],
  assurance: [StageVM['state'], string],
): StageVM[] => [
  { name: 'Intent', state: 'done', caption: intent },
  { name: 'Authorization', state: 'done', caption: auth },
  { name: 'Execution', state: 'done', caption: exec },
  { name: 'Effect', state: 'done', caption: 'Declarado: sucesso' },
  { name: 'Evidence', state: evidence[0], caption: evidence[1] },
  { name: 'Assurance', state: assurance[0], caption: assurance[1] },
];

const claimTech = (claimId: string) => [
  { label: 'claim_id', value: claimId },
  { label: 'claimant', value: CLAIMANT },
  { label: 'gate', value: 'RESOURCE_BINDING' },
];

const okInvariants = [
  { label: 'invariante', value: 'path_open_calls == 0' },
  { label: 'identidade', value: 'autorizada = observada' },
  { label: 'correlação', value: 'execution_id ✓ authorization_id ✓' },
];

export const EXECUTIONS: ExecutionViewModel[] = [
  {
    id: 'ex-0412',
    executionId: 'exec_8f21c0',
    title: 'Ler relatório trimestral',
    when: 'hoje 10:12',
    action: 'fs.read',
    resource: 'D:\\workspace-iaos\\relatorios\\2026-Q3.xlsx',
    state: 'escape',
    stateLabel: 'Silent escape',
    stages: stages('Ler relatório', 'auth_5d09a7', '0:43', ['done', 'Disponível'], ['alert', 'Silent escape']),
    claim: {
      pillLabel: 'PASS declarado',
      pillKind: 'neutral',
      summary: 'O runtime declarou que a leitura teve sucesso.',
      tech: claimTech('claim_3b7e1d'),
    },
    evidence: {
      available: true,
      pillLabel: 'Disponível',
      pillKind: 'neutral',
      heading: 'Evidência disponível',
      summary: '5 observações exportadas · 1 anomalia: o caminho foi reaberto durante a execução.',
      tech: [
        { label: 'path_open_calls_during_exec', value: '1', emphasis: true },
        { label: 'bound_handle_reads', value: '1' },
        { label: 'authorized_resource_identity', value: 'vol:7a1c/00412' },
        { label: 'observed_resource_identity', value: 'vol:7a1c/00412' },
        { label: 'effect_succeeded', value: 'true' },
      ],
    },
    verification: {
      heading: 'Auditoria verificou',
      subheading: 'A métrica de vínculo de recurso avaliou a evidência.',
      pillLabel: 'Falha verificada',
      scope: SCOPE,
      summary: 'A auditoria não confirmou o resultado: o recurso foi reaberto por caminho depois da autorização.',
      tech: [
        { label: 'invariante', value: 'path_open_calls == 0', emphasis: true },
        { label: 'resultado', value: 'violada · obs. 1', emphasis: true },
        { label: 'correlação', value: 'execution_id ✓ authorization_id ✓' },
      ],
    },
    silentEscape: {
      explanation:
        'O runtime declarou sucesso, mas a auditoria não confirmou. Esta execução não deve ser tratada como bem-sucedida.',
      claimedLabel: 'PASS declarado',
      claimedNote: 'Afirmação do runtime, sem prova',
      verifiedLabel: 'FAIL verificado',
      causeText:
        'O caminho do arquivo foi reaberto durante a execução. O esperado era nenhuma abertura; foram observadas 1.',
      correlatedTitle: 'Ler relatório trimestral',
      correlatedNote: 'hoje 10:12 · autorização concedida',
      tech: [
        { label: 'execution_id', value: 'exec_8f21c0' },
        { label: 'authorization_id', value: 'auth_5d09a7' },
        { label: 'claim_id', value: 'claim_3b7e1d' },
        { label: 'invariante', value: 'path_open_calls_during_exec == 0', emphasis: true },
        { label: 'observado', value: '1', emphasis: true },
      ],
    },
  },
  {
    id: 'ex-0398',
    executionId: 'exec_71aa9b',
    title: 'Resumir pasta de pesquisa',
    when: 'hoje 09:31',
    action: 'fs.read',
    resource: 'D:\\workspace-iaos\\pesquisa\\notas.md',
    state: 'verified',
    stateLabel: 'Verificado no escopo',
    stages: stages('Resumir pasta', 'auth_2c44f0', '0:27', ['done', 'Disponível'], ['done', 'Verificado no escopo']),
    claim: {
      pillLabel: 'PASS declarado',
      pillKind: 'neutral',
      summary: 'O runtime declarou que a leitura teve sucesso.',
      tech: claimTech('claim_90c2aa'),
    },
    evidence: {
      available: true,
      pillLabel: 'Disponível',
      pillKind: 'neutral',
      heading: 'Evidência disponível',
      summary: '5 observações exportadas · nenhuma anomalia.',
      tech: [
        { label: 'path_open_calls_during_exec', value: '0' },
        { label: 'bound_handle_reads', value: '1' },
        { label: 'authorized_resource_identity', value: 'vol:7a1c/00188' },
        { label: 'observed_resource_identity', value: 'vol:7a1c/00188' },
        { label: 'effect_succeeded', value: 'true' },
      ],
    },
    verification: {
      heading: 'Auditoria verificou',
      subheading: 'Invariantes de vínculo de recurso satisfeitos.',
      pillLabel: 'Verificado no escopo',
      scope: SCOPE,
      summary: 'A leitura usou apenas o recurso autorizado, sem reabrir o caminho.',
      tech: okInvariants,
      footnote:
        'Vale apenas para o escopo acima e para a evidência exportada. Não garante outros sistemas, volumes ou ações.',
    },
  },
  {
    id: 'ex-0391',
    executionId: 'exec_4e0d12',
    title: 'Sincronizar calendário',
    when: 'hoje 08:55',
    action: 'calendar.sync',
    resource: 'calendar://primary',
    state: 'pending',
    stateLabel: 'Verificação pendente',
    stages: stages('Sincronizar', 'auth_ab31e8', '0:18', ['wait', 'Ainda não exportada'], ['wait', 'Verificação pendente']),
    claim: {
      pillLabel: 'Declarado · não verificado',
      pillKind: 'pending',
      summary: 'O runtime declarou que a sincronização teve sucesso.',
      tech: claimTech('claim_11d7c4'),
    },
    evidence: {
      available: false,
      heading: 'Sem evidência exportada',
      summary: 'Nenhuma observação exportada ainda.',
      tech: [],
    },
    verification: {
      heading: 'Verificação pendente',
      subheading: 'A auditoria ainda não rodou.',
      pillLabel: 'Verificação pendente',
      summary: 'A auditoria ainda não rodou para esta execução.',
      tech: [],
      footnote: 'Até haver verificação, trate o resultado como não confirmado.',
    },
  },
  {
    id: 'ex-0377',
    executionId: 'exec_c93b55',
    title: 'Importar planilha de custos',
    when: 'ontem 17:40',
    action: 'fs.read',
    resource: 'D:\\workspace-iaos\\custos\\2026.csv',
    state: 'insufficient',
    stateLabel: 'Evidência insuficiente',
    stages: stages('Importar', 'auth_77f1d2', '1:02', ['warn', 'Incompleta'], ['warn', 'Evidência insuficiente']),
    claim: {
      pillLabel: 'PASS declarado',
      pillKind: 'neutral',
      summary: 'O runtime declarou que a leitura teve sucesso.',
      tech: claimTech('claim_6a02be'),
    },
    evidence: {
      available: true,
      pillLabel: 'Incompleta',
      pillKind: 'insufficient',
      heading: 'Evidência disponível',
      summary: '3 de 5 observações disponíveis · faltam campos essenciais.',
      caution: 'Ausência de dados não é prova de falha nem de sucesso.',
      tech: [
        { label: 'path_open_calls_during_exec', value: '—' },
        { label: 'bound_handle_reads', value: '—' },
        { label: 'authorized_resource_identity', value: 'vol:7a1c/00301' },
        { label: 'observed_resource_identity', value: 'ausente' },
        { label: 'effect_succeeded', value: 'true' },
      ],
    },
    verification: {
      heading: 'Evidência insuficiente',
      subheading: 'A métrica não pôde avaliar com os dados exportados.',
      pillLabel: 'Evidência insuficiente',
      scope: SCOPE,
      summary: 'Faltam fatos para concluir. Isto não é uma falha confirmada.',
      tech: [],
    },
  },
  {
    id: 'ex-0360',
    executionId: 'exec_09be77',
    title: 'Ler log em volume Linux',
    when: 'ontem 11:05',
    action: 'fs.read',
    resource: '/mnt/dados/app.log',
    state: 'unsupported',
    stateLabel: 'Fora do escopo auditado',
    stages: stages('Ler log', 'auth_e1c8a3', '0:12', ['done', 'Disponível'], ['na', 'Fora do escopo auditado']),
    claim: {
      pillLabel: 'PASS declarado',
      pillKind: 'neutral',
      summary: 'O runtime declarou que a leitura teve sucesso.',
      tech: claimTech('claim_f25d90'),
    },
    evidence: {
      available: true,
      pillLabel: 'Disponível',
      pillKind: 'neutral',
      heading: 'Evidência disponível',
      summary: '4 observações exportadas em um volume Linux.',
      tech: [
        { label: 'platform', value: 'linux' },
        { label: 'filesystem', value: 'ext4' },
        { label: 'bound_handle_reads', value: '1' },
        { label: 'effect_succeeded', value: 'true' },
      ],
    },
    verification: {
      heading: 'Fora do escopo auditado',
      subheading: 'Nenhuma métrica validada cobre este escopo.',
      pillLabel: 'Fora do escopo auditado',
      scope: 'linux / ext4 / fs.read',
      summary:
        'Nenhuma métrica validada cobre este escopo. Não é uma falha: é ausência de avaliação. Escopo validado: Windows / NTFS / fs.read.',
      tech: [],
    },
  },
];
