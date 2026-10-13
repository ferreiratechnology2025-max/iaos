import { afterEach, describe, expect, it, vi } from 'vitest';
import { HttpSkillRuntime } from '../src/data/skills/HttpSkillRuntime';
import { SkillRuntimeError } from '../src/data/ports/skills';

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));

afterEach(() => vi.unstubAllGlobals());

describe('IAOS_SKILL_UI_VERTICAL_SLICE: HttpSkillRuntime', () => {
  it('lists skills from the same-origin bridge path', async () => {
    const f = vi.fn((_u: string) => json(200, { skills: [{ id: 'code-reviewer', name: 'Code Reviewer', source: 's', extra: 1 }] }));
    vi.stubGlobal('fetch', f);
    const out = await new HttpSkillRuntime().listSkills();
    expect(out).toEqual([{ id: 'code-reviewer', name: 'Code Reviewer', source: 's' }]);
    expect(f.mock.calls[0][0]).toBe('/skill-api/skills');
  });

  it('runs with a skill: posts task + skill_id (+ no files), maps the answer and metadata', async () => {
    const f = vi.fn((_u: string, _i: RequestInit) =>
      json(200, { response: 'ok', skill_id: 'code-reviewer', skill_name: 'Code Reviewer', provider: 'Agnes AI',
        returned_model: 'agnes-2.5-flash', request_id: 'r1', usage: { prompt_tokens: 7 }, files: [] }));
    vi.stubGlobal('fetch', f);
    const r = await new HttpSkillRuntime().run({ task: 't', skillId: 'code-reviewer', files: [], document: null });
    expect(JSON.parse(String(f.mock.calls[0][1].body))).toEqual({ task: 't', skill_id: 'code-reviewer', files: [], document: null });
    expect(f.mock.calls[0][0]).toBe('/skill-api/run');
    expect(r).toMatchObject({ response: 'ok', skillName: 'Code Reviewer', provider: 'Agnes AI', model: 'agnes-2.5-flash', promptTokens: 7, files: [] });
  });

  it('runs without a skill: skill_id is null', async () => {
    const f = vi.fn((_u: string, _i: RequestInit) => json(200, { response: 'x', skill_id: null, skill_name: null, provider: 'P', returned_model: 'm' }));
    vi.stubGlobal('fetch', f);
    const r = await new HttpSkillRuntime().run({ task: 't', skillId: null, files: [], document: null });
    expect(JSON.parse(String(f.mock.calls[0][1].body)).skill_id).toBeNull();
    expect(r.skillName).toBeNull();
  });

  it('sends only file REFERENCES (relative paths, in order), never content, and maps each governed-read record', async () => {
    const meta = (path: string) => ({ path, sha256: 'abc', chars: 12, governed_action: 'fs.read' });
    const f = vi.fn((_u: string, _i: RequestInit) =>
      json(200, { response: 'ok', provider: 'P', returned_model: 'm', files: [meta('src/payment.ts'), meta('src/db.ts'), meta('tests/api.test.ts')] }));
    vi.stubGlobal('fetch', f);
    const paths = ['src/payment.ts', 'src/db.ts', 'tests/api.test.ts'];
    const r = await new HttpSkillRuntime().run({ task: 't', skillId: null, files: paths, document: null });
    expect(JSON.parse(String(f.mock.calls[0][1].body))).toEqual({ task: 't', skill_id: null, files: paths, document: null });
    expect(r.files.map((x) => x.path)).toEqual(paths);
    expect(r.files[0]).toEqual({ path: 'src/payment.ts', sha256: 'abc', chars: 12, governedAction: 'fs.read' });
  });

  it('a rejected file aborts the request and names the file (no partial result)', async () => {
    vi.stubGlobal('fetch', () => json(403, { error: 'read_denied', detail: 'resource_outside_root', index: 2, path: '../x.txt' }));
    await expect(new HttpSkillRuntime().run({ task: 't', skillId: null, files: ['a', 'b', '../x.txt'], document: null })).rejects.toMatchObject({
      kind: 'read_denied',
      path: '../x.txt',
    });
    vi.stubGlobal('fetch', () => json(413, { error: 'context_too_large', index: 1, path: 'b' }));
    await expect(new HttpSkillRuntime().run({ task: 't', skillId: null, files: ['a', 'b'], document: null })).rejects.toMatchObject({ kind: 'context_too_large' });
  });

  it('sends only a document REFERENCE and maps the governed fs.read_bytes record', async () => {
    const f = vi.fn((_u: string, _i: RequestInit) =>
      json(200, { response: 'ok', provider: 'P', returned_model: 'm', files: [],
        document: { path: 'contrato.pdf', kind: 'pdf', bytes: 4096, sha256: 'abc', pages: 3, extracted_chars: 900, governed_action: 'fs.read_bytes' } }));
    vi.stubGlobal('fetch', f);
    const r = await new HttpSkillRuntime().run({ task: 't', skillId: null, files: [], document: 'contrato.pdf' });
    expect(JSON.parse(String(f.mock.calls[0][1].body))).toEqual({ task: 't', skill_id: null, files: [], document: 'contrato.pdf' });
    expect(r.document).toEqual({ path: 'contrato.pdf', kind: 'pdf', bytes: 4096, sha256: 'abc', pages: 3, extractedChars: 900, governedAction: 'fs.read_bytes' });
  });

  it('maps an image document (no pages, no extracted text) and a missing document to null', async () => {
    vi.stubGlobal('fetch', () => json(200, { response: 'ok', provider: 'P', returned_model: 'm',
      document: { path: 'a.png', kind: 'image/png', bytes: 10, sha256: 's', pages: null, extracted_chars: null, governed_action: 'fs.read_bytes' } }));
    const rt = new HttpSkillRuntime();
    const img = await rt.run({ task: 't', skillId: null, files: [], document: 'a.png' });
    expect(img.document).toMatchObject({ kind: 'image/png', pages: null, extractedChars: null });
    vi.stubGlobal('fetch', () => json(200, { response: 'ok', provider: 'P', returned_model: 'm', document: null }));
    expect((await rt.run({ task: 't', skillId: null, files: [], document: null })).document).toBeNull();
  });

  it('maps the document errors; a rejected document names the path and returns no result', async () => {
    const rt = new HttpSkillRuntime();
    const req = { task: 't', skillId: null, files: [] as string[], document: 'x.pdf' };
    for (const [status, code] of [[415, 'unsupported_document'], [422, 'invalid_document'], [422, 'document_no_text'], [413, 'document_too_large'],
      [413, 'context_too_large'], [503, 'document_support_unavailable']] as const) {
      vi.stubGlobal('fetch', () => json(status, { error: code, path: 'x.pdf' }));
      await expect(rt.run(req)).rejects.toMatchObject({ kind: code, path: 'x.pdf' });
    }
    vi.stubGlobal('fetch', () => json(403, { error: 'read_denied', detail: 'resource_outside_root', path: '../x.pdf' }));
    await expect(rt.run({ ...req, document: '../x.pdf' })).rejects.toMatchObject({ kind: 'read_denied', path: '../x.pdf' });
  });

  it('reads the workspace root (null when file context is disabled)', async () => {
    const rt = new HttpSkillRuntime();
    vi.stubGlobal('fetch', vi.fn((_u: string) => json(200, { root: 'D:/proj' })));
    expect(await rt.getWorkspace()).toEqual({ root: 'D:/proj' });
    vi.stubGlobal('fetch', vi.fn((_u: string) => json(200, { root: null })));
    expect(await rt.getWorkspace()).toEqual({ root: null });
  });

  it('maps bridge errors and an unreachable bridge to typed errors', async () => {
    const rt = new HttpSkillRuntime();
    const req = { task: 't', skillId: null, files: [], document: null };
    vi.stubGlobal('fetch', () => json(404, { error: 'unknown_skill' }));
    await expect(rt.run({ ...req, skillId: 'x' })).rejects.toMatchObject({ kind: 'unknown_skill' });
    vi.stubGlobal('fetch', () => json(503, { error: 'not_configured' }));
    await expect(rt.run(req)).rejects.toMatchObject({ kind: 'not_configured' });
    vi.stubGlobal('fetch', () => json(403, { error: 'read_denied', detail: 'canonicalization_rejected' }));
    await expect(rt.run({ ...req, files: ['../x'] })).rejects.toMatchObject({ kind: 'read_denied' });
    vi.stubGlobal('fetch', () => json(404, { error: 'file_not_found' }));
    await expect(rt.run({ ...req, files: ['nope'] })).rejects.toMatchObject({ kind: 'file_not_found' });
    vi.stubGlobal('fetch', () => json(413, { error: 'file_too_large' }));
    await expect(rt.run({ ...req, files: ['big'] })).rejects.toMatchObject({ kind: 'file_too_large' });
    vi.stubGlobal('fetch', () => json(503, { error: 'workspace_not_configured' }));
    await expect(rt.run({ ...req, files: ['a'] })).rejects.toMatchObject({ kind: 'workspace_not_configured' });
    vi.stubGlobal('fetch', () => json(502, { error: 'upstream', detail: 'HTTP 401' }));
    await expect(rt.run(req)).rejects.toBeInstanceOf(SkillRuntimeError);
    vi.stubGlobal('fetch', () => Promise.resolve(new Response('', { status: 500 })));
    await expect(rt.listSkills()).rejects.toMatchObject({ kind: 'offline' });
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('failed')));
    await expect(rt.run(req)).rejects.toMatchObject({ kind: 'offline' });
  });
});
