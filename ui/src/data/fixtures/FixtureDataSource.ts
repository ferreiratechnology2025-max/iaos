import type {
  AuthorityVM,
  ExecutionViewModel,
  HomeVM,
  ModelChecks,
  ModelProfileVM,
  ModelSettingsVM,
  ShellVM,
} from '../../contracts/viewmodels';
import type { IaosDataPort } from '../ports/ports';
import { EXECUTIONS } from './executions';

/**
 * FIXTURE DATA ONLY. No network, no filesystem, no backend.
 * Credentials are references (credential://...), never secrets.
 */

const SCOPE = 'Windows / NTFS / fs.read';

const SHELL: ShellVM = {
  workspacePath: 'D:\\workspace-iaos',
  recents: EXECUTIONS.map((e) => ({
    id: e.id,
    title: e.title,
    state: e.state,
    stateLabel: e.stateLabel,
    duration: e.stages[2].caption,
  })),
};

const HOME: HomeVM = {
  starts: [
    { index: '01', label: 'Mapear este workspace', href: '#/execution/ex-0398' },
    { index: '02', label: 'Revisar permissões e capacidades', href: '#/authority' },
    { index: '03', label: 'Ver a última execução auditada', href: '#/execution/ex-0412' },
  ],
  flow: [
    { name: 'Intent', caption: 'Você descreve o objetivo' },
    { name: 'Authorization', caption: 'Governance decide' },
    { name: 'Execution', caption: 'Runtime executa' },
    { name: 'Effect', caption: 'Efeito no recurso' },
    { name: 'Evidence', caption: 'Evidência exportada' },
    { name: 'Assurance', caption: 'Auditoria verifica' },
  ],
  summary: [
    { value: '18', label: 'Verificadas no escopo' },
    { value: '2', label: 'Verificação pendente' },
    { value: '1', label: 'Evidência insuficiente', hint: 'deveria ser avaliável' },
    { value: '1', label: 'Fora do escopo auditado', hint: 'sem métrica para o domínio' },
    { value: '1', label: 'Silent escape', hint: 'últimos 30 dias', tone: 'escape', href: '#/execution/ex-0412' },
  ],
};

const AUTHORITY: AuthorityVM = {
  capabilities: [
    { id: 'fs', name: 'Filesystem', actionCount: '3 ações' },
    { id: 'net', name: 'Rede', actionCount: '2 ações' },
    { id: 'shell', name: 'Shell', actionCount: '1 ação' },
    { id: 'cal', name: 'Calendário', actionCount: '2 ações' },
    { id: 'mem', name: 'Memória', actionCount: '2 ações' },
  ],
  selectedId: 'fs',
  selectedName: 'Filesystem',
  policies: [
    {
      action: 'READ',
      policyKind: 'auto',
      policyLabel: 'Permitir automaticamente',
      coverage: { kind: 'validated', label: 'Cobertura validada', note: `Escopo: ${SCOPE}` },
    },
    {
      action: 'WRITE',
      policyKind: 'ask',
      policyLabel: 'Perguntar sempre',
      coverage: { kind: 'none', label: 'Sem cobertura', note: 'Nenhuma métrica validada para fs.write' },
    },
    {
      action: 'DELETE',
      policyKind: 'never',
      policyLabel: 'Nunca',
      coverage: { kind: 'none', label: 'Sem cobertura', note: 'Bloqueada por política; sem auditoria aplicável' },
    },
  ],
  scopePath: 'D:\\workspace-iaos',
  lastChange: '03 out 2026 · por você',
  stats: [
    { value: '183', label: 'Leituras' },
    { value: '7', label: 'Escritas' },
    { value: '4', label: 'Negadas pela Governance' },
    { value: '1', label: 'Silent escape', tone: 'escape', href: '#/execution/ex-0412' },
  ],
};

const PROFILES: ModelProfileVM[] = [
  {
    id: 'atomic',
    name: 'Atomic Chat Local',
    transport: 'OPENAI_COMPATIBLE',
    location: 'LOCAL',
    runtimeName: 'Atomic Chat',
    baseUrl: 'http://localhost:1337/v1',
    apiKeyRef: '',
    model: 'Qwen 3',
    enabled: true,
    checks: { endpoint: 'ok', creds: 'na', model: 'ok' },
  },
  {
    id: 'gpu',
    name: 'GPU Casa',
    transport: 'OPENAI_COMPATIBLE',
    location: 'LAN',
    runtimeName: 'vLLM',
    baseUrl: 'http://192.168.x.x:8000/v1',
    apiKeyRef: 'credential://models/gpu-casa',
    model: 'Llama 3.3 70B',
    enabled: true,
    checks: null,
  },
  {
    id: 'cloud',
    name: 'Cloud',
    transport: 'OPENAI_COMPATIBLE',
    location: 'CLOUD',
    runtimeName: 'Provedor remoto',
    baseUrl: 'https://provider.example/v1',
    apiKeyRef: 'credential://models/cloud-primary',
    model: 'GPT-5.6',
    enabled: true,
    checks: { endpoint: 'ok', creds: 'ok', model: 'fail' },
  },
];

/** Canned results for the simulated "Testar conexão". */
const TEST_RESULTS: Record<string, ModelChecks> = {
  atomic: { endpoint: 'ok', creds: 'na', model: 'ok' },
  gpu: { endpoint: 'ok', creds: 'ok', model: 'ok' },
  cloud: { endpoint: 'ok', creds: 'ok', model: 'fail' },
};

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export class FixtureDataSource implements IaosDataPort {
  async getShell(): Promise<ShellVM> {
    return SHELL;
  }
  async getHome(): Promise<HomeVM> {
    return HOME;
  }
  async getAuthority(): Promise<AuthorityVM> {
    return AUTHORITY;
  }
  async getExecution(id: string): Promise<ExecutionViewModel | null> {
    return EXECUTIONS.find((e) => e.id === id) ?? null;
  }
  async getModelSettings(): Promise<ModelSettingsVM> {
    return { profiles: PROFILES.map((p) => ({ ...p })), activeId: 'atomic' };
  }
  async testModelProfile(profile: ModelProfileVM): Promise<ModelChecks> {
    await delay(900); // simulated latency only; no network
    return TEST_RESULTS[profile.id] ?? { endpoint: 'ok', creds: 'ok', model: 'ok' };
  }
}
