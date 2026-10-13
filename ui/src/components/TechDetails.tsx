import type { TechRowVM } from '../contracts/viewmodels';

export function TechDetails({ rows, open = false }: { rows: TechRowVM[]; open?: boolean }) {
  if (rows.length === 0) return null;
  return (
    <details className="tech" open={open}>
      <summary>Ver detalhes técnicos</summary>
      <div className="kv">
        {rows.map((r) => (
          <div key={r.label}>
            <span>{r.label}</span>
            <b className={r.emphasis ? 'bad' : undefined}>{r.value}</b>
          </div>
        ))}
      </div>
    </details>
  );
}
