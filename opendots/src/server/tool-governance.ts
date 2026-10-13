import { createHash } from 'node:crypto';

export type ToolGovernanceContext = {
  dotId?: string;
  threadId?: string;
  actor?: string;
  resource?: string;
  effectClass?: 'OBSERVE' | 'EFFECT';
  evidenceMode?: 'DIGEST_ONLY' | 'STRUCTURED';
};

type MediationResult = { status: string; [key: string]: unknown };

function safeResultIdentity(result: unknown): Record<string, string | number | boolean> {
  if (result === null || typeof result !== 'object' || Array.isArray(result)) return {};
  return Object.fromEntries(
    Object.entries(result as Record<string, unknown>).filter(
      ([key, value]) =>
        (key === 'revision' || key === 'id' || key.endsWith('Id') || key.endsWith('_id')) &&
        (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'),
    ),
  ) as Record<string, string | number | boolean>;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

function digestArgs(args: unknown): string {
  return createHash('sha256').update(stableJson(args)).digest('hex');
}

function digestResult(result: unknown): string {
  return createHash('sha256').update(stableJson(result)).digest('hex');
}

export async function governToolExecution<T>(options: {
  toolName: string;
  args: unknown;
  context?: ToolGovernanceContext;
  executeOriginal: () => Promise<T> | T;
}): Promise<T> {
  const gateway = process.env.IAOS_TOOL_GATEWAY_URL;
  if (!gateway) return options.executeOriginal();
  const context = options.context ?? {};
  const mediation = await fetch(`${gateway.replace(/\/$/, '')}/tool-invocation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tool_name: options.toolName,
      tool_namespace: 'opendots',
      arguments_digest: digestArgs(options.args),
      resource: context.resource ?? `dot/${context.dotId ?? 'unknown'}`,
      actor: context.actor ?? 'agent',
      context: context.threadId ?? 'unknown',
      effect_class: context.effectClass ?? 'EFFECT',
      evidence_mode: context.evidenceMode ?? 'DIGEST_ONLY',
    }),
  });
  const result = (await mediation.json()) as MediationResult;
  if (!mediation.ok || (result.status !== 'MEDIATED' && result.status !== 'AUTHORIZED') || result.decision !== 'ALLOW')
    throw new Error('iaos_tool_mediation_rejected');
  const evidenceMode = context.evidenceMode ?? 'DIGEST_ONLY';
  const authorizedDigest = result.arguments_digest;
  const preExecDigest = digestArgs(options.args);
  if (authorizedDigest !== preExecDigest) throw new Error('iaos_pre_exec_binding_mismatch');
  const value = await options.executeOriginal();
  const resultDigest = digestResult(value);
  const receipt = await fetch(`${gateway.replace(/\/$/, '')}/tool-receipt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      authorization_id: result.authorization_id,
      execution_id: result.execution_id,
      invocation_id: result.invocation_id,
      tool_name: options.toolName,
      resource: context.resource ?? `dot/${context.dotId ?? 'unknown'}`,
      arguments_digest: preExecDigest,
      result_digest: resultDigest,
      evidence_mode: evidenceMode,
      ...(evidenceMode === 'STRUCTURED' ? { structured_evidence: value } : {}),
      result_identity: safeResultIdentity(value),
    }),
  });
  if (!receipt.ok) throw new Error('iaos_receipt_incomplete');
  return value;
}
