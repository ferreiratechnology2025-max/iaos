import type { StageState } from '../contracts/viewmodels';

export interface FlowItem {
  name: string;
  caption: string;
  state?: StageState;
}

/** Intent -> Authorization -> Execution -> Effect -> Evidence -> Assurance. */
export function FlowStrip({ items }: { items: FlowItem[] }) {
  return (
    <div className="flow">
      {items.map((s) => (
        <div key={s.name} className={`stg ${s.state ?? ''}`}>
          <span className="dot" />
          <b>{s.name}</b>
          <small>{s.caption}</small>
        </div>
      ))}
    </div>
  );
}
