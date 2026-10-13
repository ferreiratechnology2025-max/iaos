import { ModelStatus } from './ModelStatus';

export function TopBar({ title, subtitle, showModel = false, simulated = true }: { title: string; subtitle: string; showModel?: boolean; simulated?: boolean }) {
  return (
    <div className="top">
      <div className="t">
        <b>{title}</b>
        <small>{subtitle}</small>
      </div>
      <div className="r">
        {showModel && <ModelStatus />}
        {simulated && (
          <span className="chip" title="Dados simulados">
            Dados simulados
          </span>
        )}
      </div>
    </div>
  );
}
