import { SkillRuntimeError } from '../ports/skills';
import type {
  SkillErrorKind,
  SkillOption,
  SkillRunDocument,
  SkillRunFile,
  SkillRunRequest,
  SkillRunResult,
  SkillRuntimePort,
  WorkspaceInfo,
} from '../ports/skills';

/**
 * The only module in src allowed to use fetch (see tests/gate.test.ts). It talks to a same-origin path,
 * `/skill-api`, which the dev/preview server forwards to the loopback Skill bridge. The credential lives in the
 * bridge's environment and files are read by the backend through governance; this code never sees either.
 */
const BASE = '/skill-api';
const RUN_TIMEOUT_MS = 180_000;
const KNOWN: SkillErrorKind[] = [
  'not_configured',
  'unknown_skill',
  'invalid',
  'upstream',
  'read_denied',
  'file_not_found',
  'file_too_large',
  'context_too_large',
  'unsupported_document',
  'invalid_document',
  'document_no_text',
  'document_too_large',
  'document_support_unavailable',
  'file_unreadable',
  'workspace_not_configured',
];

async function readJson(res: Response): Promise<Record<string, unknown> | null> {
  try {
    const v = await res.json();
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function toError(res: Response, body: Record<string, unknown> | null): SkillRuntimeError {
  const code = typeof body?.error === 'string' ? body.error : '';
  if (KNOWN.includes(code as SkillErrorKind)) {
    const detail = typeof body?.detail === 'string' ? ` (${body.detail})` : '';
    const path = typeof body?.path === 'string' ? body.path : undefined;
    return new SkillRuntimeError(code as SkillErrorKind, code + detail, path);
  }
  // No structured error: the forwarding target is not answering (bridge not running).
  return new SkillRuntimeError('offline', `bridge unavailable (HTTP ${res.status})`);
}

function mapFile(v: unknown): SkillRunFile | null {
  const f = v as { path?: string; sha256?: string; chars?: number; governed_action?: string } | null;
  if (!f || typeof f.path !== 'string') return null;
  return { path: f.path, sha256: String(f.sha256 ?? ''), chars: Number(f.chars ?? 0), governedAction: String(f.governed_action ?? '') };
}

function mapDocument(v: unknown): SkillRunDocument | null {
  const d = v as {
    path?: string; kind?: string; bytes?: number; sha256?: string; pages?: number | null;
    extracted_chars?: number | null; governed_action?: string;
  } | null;
  if (!d || typeof d.path !== 'string') return null;
  return {
    path: d.path,
    kind: String(d.kind ?? ''),
    bytes: Number(d.bytes ?? 0),
    sha256: String(d.sha256 ?? ''),
    pages: typeof d.pages === 'number' ? d.pages : null,
    extractedChars: typeof d.extracted_chars === 'number' ? d.extracted_chars : null,
    governedAction: String(d.governed_action ?? ''),
  };
}

export class HttpSkillRuntime implements SkillRuntimePort {
  private static async settle(pending: Promise<Response>): Promise<{ res: Response; body: Record<string, unknown> | null }> {
    let res: Response;
    try {
      res = await pending;
    } catch {
      throw new SkillRuntimeError('offline', 'bridge unreachable');
    }
    return { res, body: await readJson(res) };
  }

  async listSkills(): Promise<SkillOption[]> {
    const { res, body } = await HttpSkillRuntime.settle(fetch(`${BASE}/skills`, { headers: { Accept: 'application/json' } }));
    if (!res.ok || !Array.isArray(body?.skills)) throw toError(res, body);
    return (body!.skills as SkillOption[]).map((s) => ({ id: s.id, name: s.name, source: s.source }));
  }

  async getWorkspace(): Promise<WorkspaceInfo> {
    const { res, body } = await HttpSkillRuntime.settle(fetch(`${BASE}/workspace`, { headers: { Accept: 'application/json' } }));
    if (!res.ok || !body || !('root' in body)) throw toError(res, body);
    return { root: typeof body.root === 'string' ? body.root : null };
  }

  async run(req: SkillRunRequest): Promise<SkillRunResult> {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), RUN_TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetch(`${BASE}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ task: req.task, skill_id: req.skillId, files: req.files, document: req.document }),
        signal: ctl.signal,
      });
    } catch (e) {
      throw e instanceof DOMException && e.name === 'AbortError'
        ? new SkillRuntimeError('timeout', 'no answer within the time limit')
        : new SkillRuntimeError('offline', 'bridge unreachable');
    } finally {
      clearTimeout(timer);
    }
    const b = await readJson(res);
    if (!res.ok || typeof b?.response !== 'string') throw toError(res, b);
    const usage = b.usage as { prompt_tokens?: number } | null | undefined;
    return {
      response: b.response,
      skillId: (b.skill_id as string | null) ?? null,
      skillName: (b.skill_name as string | null) ?? null,
      provider: String(b.provider ?? ''),
      model: String(b.returned_model ?? b.requested_model ?? ''),
      requestId: (b.request_id as string | null) ?? null,
      promptTokens: typeof usage?.prompt_tokens === 'number' ? usage.prompt_tokens : null,
      document: mapDocument(b.document),
      files: Array.isArray(b.files) ? b.files.map(mapFile).filter((f): f is SkillRunFile => f !== null) : [],
    };
  }
}
