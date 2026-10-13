import { useEffect, useState } from 'react';
import { TopBar } from '../../components/TopBar';
import { dailyBriefClient, type DailyBriefRunDetailVM, type DailyBriefRunSummaryVM } from '../../data/daily-brief/DailyBriefClient';

type HistoryState = 'LOADING' | 'READY' | 'ERROR';

export function DailyBriefHistoryScreen({ runId }: { runId?: string }) {
  const [state, setState] = useState<HistoryState>('LOADING');
  const [runs, setRuns] = useState<DailyBriefRunSummaryVM[]>([]);
  const [detail, setDetail] = useState<DailyBriefRunDetailVM | null>(null);

  useEffect(() => {
    let active = true;
    setState('LOADING');
    const load = async () => {
      try {
        const history = await dailyBriefClient.listHistory();
        if (!active) return;
        setRuns(history);
        if (runId) {
          const selected = await dailyBriefClient.getHistory(runId);
          if (!active) return;
          setDetail(selected);
        } else {
          setDetail(null);
        }
        setState('READY');
      } catch {
        if (active) setState('ERROR');
      }
    };
    void load();
    return () => { active = false; };
  }, [runId]);

  return (
    <>
      <TopBar title="Daily Brief" subtitle="Histórico" showModel simulated={false} />
      <div className="canvas brief-shell">
        <div className="eyebrow">Produto · Daily Brief</div>
        <div className="brief-head">
          <div>
            <h1>Histórico</h1>
            <p className="lede">Execuções anteriores do resumo, preservadas como estado de produto.</p>
          </div>
          <a className="btn ghost" href="#/daily-brief">Novo resumo</a>
        </div>
        {state === 'LOADING' && <div className="brief-empty brief-loading" role="status">Carregando histórico…</div>}
        {state === 'ERROR' && <div className="brief-error" role="alert">Não foi possível carregar o histórico agora.</div>}
        {state === 'READY' && (
          <div className="history-layout">
            <section className="history-list" aria-label="Execuções anteriores">
              <div className="eyebrow">Execuções</div>
              {runs.length === 0 && <div className="brief-empty">Nenhum resumo foi executado ainda.</div>}
              {runs.map((run) => (
                <a className={`history-item${run.run_id === runId ? ' selected' : ''}`} href={`#/daily-brief/history?run=${encodeURIComponent(run.run_id)}`} key={run.run_id}>
                  <div><strong>{run.status === 'success' ? 'Executado com sucesso' : `Falhou${run.error_code ? ` em ${run.error_code}` : ''}`}</strong><span>{formatDate(run.completed_at)} · {run.trigger === 'schedule' ? 'Rotina diária' : 'Manual'}</span></div>
                </a>
              ))}
            </section>
            {runId && detail && <HistoryDetail detail={detail} />}
          </div>
        )}
      </div>
    </>
  );
}

function HistoryDetail({ detail }: { detail: DailyBriefRunDetailVM }) {
  return (
    <section className="history-detail">
      <div className="eyebrow">Execução</div>
      <h2>{detail.status === 'success' ? 'Executado com sucesso' : 'Execução com falha'}</h2>
      <p className="history-meta">{formatDate(detail.completed_at)}</p>
      {detail.error_code && <div className="brief-error">Falhou em {detail.error_code}</div>}
      {detail.summary && <section className="brief-summary"><div className="eyebrow">Resumo</div><p>{detail.summary}</p></section>}
      <div className="brief-columns">
        <HistorySection title="Agenda" content={detail.calendar_section} />
        <HistorySection title="E-mails" content={detail.email_section} />
        <HistorySection title="Web" content={detail.web_section} />
        <HistorySection title="Scouts" content={detail.scout_section} />
      </div>
      <section className="brief-sources"><div className="eyebrow">Fontes</div><div className="source-list">{detail.source_refs.map((ref) => <code key={ref}>{ref}</code>)}</div></section>
    </section>
  );
}

function HistorySection({ title, content }: { title: string; content: string | null }) {
  return <section className="brief-card"><h2>{title}</h2><p>{content ?? 'Não disponível.'}</p></section>;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
