import type { ComputerControl } from './ComputerRuntimeClient';

/** Presentation only: a finished request's reason is history, never the current status. */
export function statusMessage(control: ComputerControl | null, streamReady: boolean): string {
  if (!streamReady) return 'Aguardando live screen\u2026';
  if (control?.reason) return control.reason;
  const request = control?.request;
  if (request && (request.status === 'waiting' || request.status === 'taken') && request.reason) return request.reason;
  return control ? (control.holder === 'human' ? 'Takeover ativo' : 'Pronto') : 'Aguardando live screen\u2026';
}