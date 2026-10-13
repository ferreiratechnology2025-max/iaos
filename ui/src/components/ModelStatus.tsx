import { useEffect, useRef } from 'react';
import { useModels } from '../app/ModelContext';
import type { ModelProfileVM } from '../contracts/viewmodels';
import { Disclosure } from './Disclosure';
import { IconChevron } from './Icons';
import { LOCATION_LABEL } from './modelCopy';

const dot = (p: ModelProfileVM) => `dot-${p.location.toLowerCase()}`;

/** Model Runtime Status: model, runtime and locality always visible. */
export function ModelStatus() {
  const m = useModels();
  const ref = useRef<HTMLSpanElement>(null);
  const active = m.profiles.find((p) => p.id === m.activeId);

  useEffect(() => {
    if (!m.popoverOpen) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) m.setPopoverOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [m, m.popoverOpen]);

  if (!active) return null;
  return (
    <span className="mstat" ref={ref}>
      <button className="chip mbtn" id="mbtn" onClick={() => m.setPopoverOpen(!m.popoverOpen)} aria-expanded={m.popoverOpen}>
        <i className={dot(active)} />
        <b>{active.model}</b>
        <span className="kind">{LOCATION_LABEL[active.location]}</span>
        <IconChevron />
      </button>
      {m.popoverOpen && (
        <div className="pop" id="mpop">
          <div className="eyebrow" style={{ padding: '4px 4px 10px' }}>
            Modelo ativo
          </div>
          {m.profiles.map((p) => (
            <button key={p.id} className={`opt${p.id === m.activeId ? ' on' : ''}`} data-id={p.id} onClick={() => m.setActive(p.id)}>
              <i className={dot(p)} />
              <div>
                <b>{p.model}</b>
                <small>
                  via {p.runtimeName} · {p.name}
                </small>
              </div>
              <span className="kind">{LOCATION_LABEL[p.location]}</span>
            </button>
          ))}
          <Disclosure location={active.location} variant="disc" />
          <div className="disc2">
            Runtime: <b>{active.runtimeName}</b> · Transporte: OpenAI-compatible. O modelo interpreta e propõe; quem
            autoriza e executa continua sendo a Governance.
          </div>
          <a className="mfoot" href="#/settings/models" style={{ display: 'block' }} onClick={() => m.setPopoverOpen(false)}>
            Configurar perfis: Configurações → IA / Modelos
          </a>
        </div>
      )}
    </span>
  );
}
