import { describe, expect, test } from 'vitest';
import { statusMessage } from '../src/data/computer/statusMessage';
import type { ComputerControl } from '../src/data/computer/ComputerRuntimeClient';

const ctl = (holder: 'bot' | 'human', status?: string, extra: object = {}): ComputerControl =>
  ({ holder, request: status ? { id: 'r', status, reason: 'Human requested takeover from IAOS' } : undefined, ...extra }) as ComputerControl;

describe('TAKEOVER_STATUS_MESSAGE_GATE', () => {
  test('bot + completed + stream ready -> Pronto (stale reason not shown)', () => {
    expect(statusMessage(ctl('bot', 'completed'), true)).toBe('Pronto');
  });
  test('bot + waiting -> request reason', () => {
    expect(statusMessage(ctl('bot', 'waiting'), true)).toBe('Human requested takeover from IAOS');
  });
  test('human + taken -> coherent active takeover message', () => {
    expect(statusMessage(ctl('human', 'taken'), true)).toBe('Human requested takeover from IAOS');
    expect(statusMessage(ctl('human'), true)).toBe('Takeover ativo');
  });
  test('stream not ready -> Aguardando live screen', () => {
    expect(statusMessage(ctl('bot', 'completed'), false)).toBe('Aguardando live screen\u2026');
  });
  test('control loaded -> never the old computer-state message', () => {
    for (const s of [true, false]) for (const h of ['bot', 'human'] as const)
      expect(statusMessage(ctl(h, 'completed'), s)).not.toMatch(/estado do computador/);
  });
});