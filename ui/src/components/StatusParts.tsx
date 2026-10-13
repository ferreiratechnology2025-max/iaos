import type { AuditState } from '../contracts/viewmodels';

/** Glyph for an audit state. The state itself comes from the view model. */
export function StateGlyph({ state }: { state: AuditState }) {
  return <span className={`g ${state}`} aria-hidden="true" />;
}

/** Pill for an audit state; label text is supplied by the view model. */
export function StatePill({ state, label }: { state: AuditState; label: string }) {
  return <span className={`pill ${state}`}>{label}</span>;
}
