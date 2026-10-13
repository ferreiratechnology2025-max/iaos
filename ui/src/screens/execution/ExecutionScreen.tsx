import { useEffect } from 'react';
import { dataSource } from '../../data';
import { useData } from '../../app/useData';
import type { AuditState, ExecutionViewModel } from '../../contracts/viewmodels';
import { FlowStrip } from '../../components/FlowStrip';
import { StatePill } from '../../components/StatusParts';
import { TechDetails } from '../../components/TechDetails';
import { TopBar } from '../../components/TopBar';

/** Presentation mapping only: which pill style a supplied state uses in the Verification column. */
const verificationPill = (s: AuditState): AuditState => (s === 'escape' ? 'fail' : s);
const withHumanSummary = (s: AuditState) => s === 'escape' || s === 'fail' || s === 'verified';

function SilentEscapeBanner({ e }: { e: NonNullable<ExecutionViewModel['silentEscape']> }) {
  return (
    <section className="banner">
      <div className="lab">
        <span className="g escape" style={{ width: 24, height: 24 }} />
        Silent escape
      </div>
      <p>{e.explanation}</p>
      <div className="vs">
        <div className="c claim">
          <small>Runtime declarou</small>
          <strong>{e.claimedLabel}</strong>
          <span>{e.claimedNote}</span>
        </div>
        <div className="ne">≠</div>
        <div className="c ver">
          <small>Auditoria verificou</small>
          <strong>{e.verifiedLabel}</strong>
        </div>
      </div>
      <div className="cause">
        <div>
          <span className="k">Evidência causadora</span>
          {e.causeText}
        </div>
        <div>
          <span className="k">Execução correlacionada</span>
          {e.correlatedTitle}
          <br />
          <span style={{ color: 'var(--muted)' }}>{e.correlatedNote}</span>
        </div>
      </div>
      <TechDetails rows={e.tech} />
    </section>
  );
}

function Layers({ x }: { x: ExecutionViewModel }) {
  const { claim, evidence, verification: v, state } = x;
  return (
    <div className="layers" style={{ marginTop: 6 }}>
      <div className="layer claimL">
        <div className="eyebrow">1 · Declaração</div>
        <h3>Runtime declarou</h3>
        <div className="sm">Afirmação do runtime. Não é evidência nem autoridade.</div>
        <div style={{ marginTop: 14 }}>
          <span className={`pill ${claim.pillKind}`}>{claim.pillLabel}</span>
        </div>
        <p className="human">{claim.summary}</p>
        <TechDetails rows={claim.tech} />
      </div>

      <div className="layer">
        <div className="eyebrow">2 · Evidência</div>
        <h3>{evidence.heading}</h3>
        {evidence.available ? (
          <>
            <div className="sm">Observações exportadas da execução, sem interpretação.</div>
            <div style={{ marginTop: 14 }}>
              <span className={`pill ${evidence.pillKind}`}>{evidence.pillLabel}</span>
            </div>
            <p className="human">{evidence.summary}</p>
            {evidence.caution && (
              <div className="empty" style={{ borderColor: 'var(--warn-line)', color: 'var(--warn)', background: 'var(--warn-bg)' }}>
                {evidence.caution}
              </div>
            )}
            <TechDetails rows={evidence.tech} />
          </>
        ) : (
          <>
            <div className="sm">Ainda não há evidência para esta execução.</div>
            <div className="empty">{evidence.summary} Isto não significa que a execução falhou ou que foi segura.</div>
          </>
        )}
      </div>

      <div className={`layer verL${state === 'escape' || state === 'fail' ? ' fail' : ''}`}>
        <div className="eyebrow">3 · Verificação</div>
        <h3>{v.heading}</h3>
        <div className="sm">{v.subheading}</div>
        {v.pillLabel && (
          <div style={{ marginTop: 14 }}>
            <StatePill state={verificationPill(state)} label={v.pillLabel} />
          </div>
        )}
        {v.scope && <div className="scopeline">Escopo · {v.scope}</div>}
        {withHumanSummary(state) ? (
          <>
            <p className="human">{v.summary}</p>
            <TechDetails rows={v.tech} />
            {v.footnote && <p className="foot" style={{ marginTop: 14 }}>{v.footnote}</p>}
          </>
        ) : (
          <div className="empty">
            {v.summary}
            {v.footnote ? ` ${v.footnote}` : ''}
          </div>
        )}
      </div>
    </div>
  );
}

export function ExecutionScreen({ id }: { id: string }) {
  const x = useData(() => dataSource.getExecution(id), [id]);
  useEffect(() => {
    if (x) document.title = `IAOS · ${x.title}`;
  }, [x]);
  if (x === undefined) return <TopBar title="Execução" subtitle="Carregando…" />;
  if (x === null) return <TopBar title="Execução não encontrada" subtitle={id} />;
  return (
    <>
      <TopBar title={x.title} subtitle={`${x.when} · ${x.action}`} />
      <div className="canvas">
        <div className="crumb">Execuções / {x.executionId}</div>
        <div className="head">
          <div>
            <h1>{x.title}</h1>
            <div className="meta">
              <span>Ação <b>{x.action}</b></span>
              <span>Recurso <b>{x.resource}</b></span>
            </div>
          </div>
          <div style={{ paddingTop: 8 }}>
            <StatePill state={x.state} label={x.stateLabel} />
          </div>
        </div>
        {x.silentEscape && <SilentEscapeBanner e={x.silentEscape} />}
        <div className="flowwrap">
          <div className="eyebrow" style={{ marginBottom: 16 }}>Sequência da execução</div>
          <FlowStrip items={x.stages.map((s) => ({ name: s.name, caption: s.caption, state: s.state }))} />
        </div>
        <Layers x={x} />
        <p className="foot">
          A interface exibe o relatório de auditoria como recebido. Nenhum estado é calculado aqui, e “Verificado” sempre é
          lido junto do escopo.
        </p>
      </div>
    </>
  );
}
