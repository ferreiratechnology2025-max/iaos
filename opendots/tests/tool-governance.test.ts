import { afterEach, expect, it, vi } from 'vitest';
import { governToolExecution } from '../src/server/tool-governance.js';
import { governanceMetadataForTool } from '../src/server/tanstack-tools.js';

afterEach(() => {
  delete process.env.IAOS_TOOL_GATEWAY_URL;
  vi.restoreAllMocks();
});

it('mediates before calling the original executor', async () => {
  process.env.IAOS_TOOL_GATEWAY_URL = 'http://127.0.0.1:8791';
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          status: 'MEDIATED',
          decision: 'ALLOW',
          arguments_digest:
            '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a',
          invocation_id: 'inv-1',
          authorization_id: 'auth-1',
          execution_id: 'exec-1',
          evidence_mode: 'DIGEST_ONLY',
        }),
        { status: 200 },
      ),
    )
    .mockResolvedValueOnce(new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const execute = vi.fn().mockResolvedValue({ spaces: [] });

  await expect(
    governToolExecution({
      toolName: 'list_authorized_spaces',
      args: {},
      context: {
        effectClass: 'OBSERVE',
        resource: 'tool/list_authorized_spaces',
      },
      executeOriginal: execute,
    }),
  ).resolves.toEqual({ spaces: [] });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(execute).toHaveBeenCalledOnce();
});

it('does not execute when the IAOS gateway rejects mediation', async () => {
  process.env.IAOS_TOOL_GATEWAY_URL = 'http://127.0.0.1:8791';
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ status: 'DENIED', decision: 'DENY' }), {
          status: 200,
        }),
      ),
  );
  const execute = vi.fn();

  await expect(
    governToolExecution({
      toolName: 'create_space_page',
      args: { title: 'blocked' },
      executeOriginal: execute,
    }),
  ).rejects.toThrow('iaos_tool_mediation_rejected');
  expect(execute).not.toHaveBeenCalled();
});

it('persists structured observation evidence before returning it', async () => {
  process.env.IAOS_TOOL_GATEWAY_URL = 'http://127.0.0.1:8791';
  const result = {
    sources: [
      { url: 'https://nasa.gov/a', title: 'A', text: 'NASA evidence' },
      { url: 'https://esa.int/b', title: 'B', text: 'ESA evidence' },
    ],
  };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          status: 'MEDIATED',
          decision: 'ALLOW',
          arguments_digest:
            '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a',
          invocation_id: 'inv-structured',
          authorization_id: 'auth-structured',
          execution_id: 'exec-structured',
          evidence_mode: 'STRUCTURED',
        }),
        { status: 200 },
      ),
    )
    .mockResolvedValueOnce(new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(
    governToolExecution({
      toolName: 'search_web',
      args: {},
      context: {
        effectClass: 'OBSERVE',
        evidenceMode: 'STRUCTURED',
        resource: 'research/thread-1',
      },
      executeOriginal: async () => result,
    }),
  ).resolves.toEqual(result);

  const receiptBody = JSON.parse(fetchMock.mock.calls[1][1].body as string);
  expect(receiptBody.evidence_mode).toBe('STRUCTURED');
  expect(receiptBody.structured_evidence).toEqual(result);
  expect(receiptBody.invocation_id).toBe('inv-structured');
  expect(receiptBody.result_digest).toHaveLength(64);
});

it('uses one trusted governance classification for research tools', () => {
  expect(governanceMetadataForTool('search_web')).toEqual({
    effectClass: 'OBSERVE',
    evidenceMode: 'STRUCTURED',
  });
  expect(governanceMetadataForTool('read_public_page')).toEqual({
    effectClass: 'OBSERVE',
    evidenceMode: 'STRUCTURED',
  });
  expect(governanceMetadataForTool('create_space_page')).toEqual({
    effectClass: 'EFFECT',
    evidenceMode: 'DIGEST_ONLY',
  });
});
