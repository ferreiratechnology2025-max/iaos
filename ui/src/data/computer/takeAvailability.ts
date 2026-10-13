import type { ComputerControl } from './ComputerRuntimeClient';

/** Assumir is available whenever the Bot holds the wheel and the live screen is ready. */
export function canTake(control: ComputerControl | null, streamReady: boolean): boolean {
  return control?.holder === 'bot' && streamReady && !control.transitioning;
}
