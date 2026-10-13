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

const IAOS_BASE = String(import.meta.env.VITE_IAOS_API_BASE_URL ?? '').replace(/\/$/, '');
const OPENDOTS_BASE = String(import.meta.env.VITE_OPENDOTS_BASE_URL ?? '').replace(/\/$/, '');

async function readJson<T>(path: string, base = IAOS_BASE): Promise<T> {
  const response = await fetch(`${base}${path}`, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`iaos_api_${response.status}`);
  return response.json() as Promise<T>;
}

type Invocation = {
  authorization_id: string;
  invocation_id: string;
  execution_id: string;
  tool_name: string;
  arguments_digest: string;
  resource: string;
  effect_class: string;
  consumed_execution_id?: string;
  receipt_status: string;
  receipt_id?: string | null;
  receipt_timestamp?: number | null;
};

export class RealIaosDataSource implements IaosDataPort {
  private async invocations(): Promise<Invocation[]> {
    const value = await readJson<{ invocations?: Invocation[] }>('/ui/invocations');
    return Array.isArray(value.invocations) ? value.invocations : [];
  }

  async getShell(): Promise<ShellVM> {
    const records = await this.invocations();
    return {
      workspacePath: 'não disponível',
      recents: records.map((record) => ({
        id: record.invocation_id,
        title: record.tool_name,
        state: record.receipt_status === 'COMPLETE' ? 'verified' : 'pending',
        stateLabel: record.receipt_status === 'COMPLETE' ? 'Com receipt' : 'Pendente',
        duration: record.effect_class,
      })),
    };
  }

  async getHome(): Promise<HomeVM> {
    const records = await this.invocations();
    const completed = records.filter((record) => record.receipt_status === 'COMPLETE').length;
    return {
      starts: [],
      flow: [
        { name: 'Intent', caption: 'Proposta registrada' },
        { name: 'Authorization', caption: 'Governance decide' },
        { name: 'Execution', caption: 'Runtime executa' },
        { name: 'Effect', caption: 'Efeito no recurso' },
        { name: 'Evidence', caption: 'Receipt ou evidência' },
        { name: 'Assurance', caption: 'Verificação disponível' },
      ],
      summary: records.length === 0 ? [] : [
        { value: String(records.length), label: 'Invocations registradas' },
        { value: String(completed), label: 'Receipts completos' },
      ],
    };
  }

  async getAuthority(): Promise<AuthorityVM> {
    const records = await this.invocations();
    return {
      capabilities: [], selectedId: '', selectedName: 'Dados de autoridade', policies: [],
      scopePath: 'não disponível', lastChange: 'não disponível',
      stats: [{ value: String(records.length), label: 'Invocations registradas' }],
      records: records.map((record) => ({
        authorizationId: record.authorization_id, invocationId: record.invocation_id, executionId: record.execution_id,
        toolName: record.tool_name, effectClass: record.effect_class, resource: record.resource,
        argumentsDigest: record.arguments_digest, status: record.receipt_status,
        consumption: record.consumed_execution_id ? 'CONSUMED' : 'NOT_CONSUMED',
        receiptId: record.receipt_id, timestamp: record.receipt_timestamp,
      })),
    };
  }

  async getExecution(id: string): Promise<ExecutionViewModel | null> {
    const records = await this.invocations();
    const record = records.find((item) => item.invocation_id === id);
    if (!record) return null;
    const complete = record.receipt_status === 'COMPLETE';
    const stages = ['Intent', 'Authorization', 'Execution', 'Effect', 'Evidence', 'Assurance']
      .map((name) => ({ name, state: complete ? 'done' as const : 'na' as const, caption: complete ? 'Registrado' : 'Não disponível' }));
    return {
      id, executionId: record.execution_id, title: record.tool_name, when: 'não disponível', action: record.tool_name,
      resource: record.resource, state: complete ? 'verified' : 'pending', stateLabel: complete ? 'Com receipt' : 'Pendente', stages,
      claim: { pillLabel: 'Dados reais', pillKind: 'neutral', summary: 'Invocation lida do gateway IAOS.', tech: [{ label: 'invocation_id', value: id }] },
      evidence: { available: complete, pillLabel: complete ? 'Receipt disponível' : 'Não disponível', pillKind: complete ? 'neutral' : 'insufficient', heading: 'Evidence', summary: complete ? 'Receipt persistido.' : 'Nenhum receipt persistido.', tech: [{ label: 'effect_class', value: record.effect_class }] },
      verification: { heading: 'Assurance', subheading: 'Estado real do gateway', summary: complete ? 'Receipt disponível para auditoria.' : 'Verificação não disponível.', tech: [{ label: 'status', value: record.receipt_status }] },
    };
  }

  async getModelSettings(): Promise<ModelSettingsVM> {
    try {
      const state = await readJson<{ settings?: { configured?: boolean; model?: string } }>('/api/state', OPENDOTS_BASE);
      const configured = Boolean(state.settings?.configured);
      const profile: ModelProfileVM = {
        id: 'opendots', name: 'OpenDots host', transport: 'OPENAI_COMPATIBLE', location: 'CLOUD',
        runtimeName: 'OpenDots', baseUrl: '', apiKeyRef: '', model: state.settings?.model ?? 'não informado',
        enabled: configured, checks: configured ? { endpoint: 'ok', creds: 'ok', model: 'ok' } : null,
      };
      return { profiles: [profile], activeId: profile.id };
    } catch {
      return { profiles: [], activeId: '' };
    }
  }

  async testModelProfile(_profile: ModelProfileVM): Promise<ModelChecks> {
    throw new Error('model_status_read_only');
  }
}
