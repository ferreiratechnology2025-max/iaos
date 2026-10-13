import { useEffect, useState } from 'react';
import { TopBar } from '../../components/TopBar';
import { dailyBriefClient, type DailyBriefResultVM, type DailyBriefRoutineVM } from '../../data/daily-brief/DailyBriefClient';

type BriefState = 'IDLE' | 'LOADING' | 'SUCCESS' | 'ERROR';

export function DailyBriefScreen() {
  const [state, setState] = useState<BriefState>('IDLE');
  const [result, setResult] = useState<DailyBriefResultVM | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    setState('LOADING');
    setError(null);
    try {
      setResult(await dailyBriefClient.run());
      setState('SUCCESS');
    } catch {
      setResult(null);
      setError('Não foi possível gerar o resumo agora.');
      setState('ERROR');
    }
  };

  return (
    <>
      <TopBar title="Daily Brief" subtitle="Resumo do dia" showModel simulated={false} />
      <div className="canvas brief-shell">
        <div className="eyebrow">Produto · Daily Brief</div>
        <div className="brief-head">
          <div>
            <h1>Daily Brief</h1>
            <p className="lede">Uma leitura curta da sua agenda, e-mails e uma fonte web escolhida para hoje.</p>
          </div>
          <button className="btn primary" disabled={state === 'LOADING'} onClick={() => void generate()}>
            {state === 'LOADING' ? 'Gerando…' : 'Gerar resumo'}
          </button>
        </div>
        <DailyBriefRoutineCard />
        <div className="brief-actions"><a className="btn ghost" href="#/daily-brief/history">Histórico</a></div>

        {state === 'IDLE' && <div className="brief-empty">Clique em <b>Gerar resumo</b> para executar o Daily Brief real.</div>}
        {state === 'LOADING' && <div className="brief-empty brief-loading" role="status" aria-live="polite">Consultando suas fontes…</div>}
        {state === 'ERROR' && <div className="brief-error" role="alert">{error}</div>}
        {state === 'SUCCESS' && result && <BriefResult result={result} />}
      </div>
    </>
  );
}

function DailyBriefRoutineCard() {
  const [routine, setRoutine] = useState<DailyBriefRoutineVM | null>(null);
  const [time, setTime] = useState('08:00');
  const [timezone, setTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
  const [enabled, setEnabled] = useState(false);
  const [state, setState] = useState<'LOADING' | 'READY' | 'SAVING' | 'ERROR'>('LOADING');

  useEffect(() => {
    let active = true;
    void dailyBriefClient.getRoutine().then((value) => {
      if (!active) return;
      if (value) {
        setRoutine(value);
        setTime(value.time);
        setTimezone(value.timezone);
        setEnabled(value.enabled);
      }
      setState('READY');
    }).catch(() => { if (active) setState('ERROR'); });
    return () => { active = false; };
  }, []);

  const save = async () => {
    setState('SAVING');
    try {
      const value = await dailyBriefClient.saveRoutine({ time, timezone, enabled });
      setRoutine(value);
      setState('READY');
    } catch {
      setState('ERROR');
    }
  };

  return (
    <section className="routine-card" aria-label="Rotina diária do Daily Brief">
      <div>
        <div className="eyebrow">Autonomia no tempo</div>
        <h2>Rotina diária</h2>
        <p className="routine-copy">Executa o mesmo Daily Brief uma vez por dia e salva o resultado no Histórico.</p>
      </div>
      <div className="routine-form">
        <label><span>Horário</span><input aria-label="Horário da rotina" type="time" value={time} disabled={state === 'LOADING' || state === 'SAVING'} onChange={(event) => setTime(event.target.value)} /></label>
        <label><span>Timezone</span><input aria-label="Timezone da rotina" value={timezone} disabled={state === 'LOADING' || state === 'SAVING'} onChange={(event) => setTimezone(event.target.value)} /></label>
        <label className="routine-toggle"><span>Ativa</span><input aria-label="Ativar rotina diária" type="checkbox" checked={enabled} disabled={state === 'LOADING' || state === 'SAVING'} onChange={(event) => setEnabled(event.target.checked)} /></label>
        <button className="btn ghost" type="button" disabled={state === 'LOADING' || state === 'SAVING'} onClick={() => void save()}>{state === 'SAVING' ? 'Salvando…' : 'Salvar rotina'}</button>
      </div>
      {state === 'ERROR' && <div className="routine-error" role="alert">Não foi possível carregar ou salvar a rotina agora.</div>}
      {state === 'READY' && routine && <div className="routine-status">{routine.enabled ? 'Rotina ativa' : 'Rotina desativada'} · {routine.time} · {routine.timezone}</div>}
    </section>
  );
}

function BriefResult({ result }: { result: DailyBriefResultVM }) {
  return (
    <div className="brief-result">
      <section className="brief-summary">
        <div className="eyebrow">Resumo</div>
        <p>{result.summary}</p>
      </section>
      <div className="brief-columns">
        <BriefSection title="Agenda" content={result.calendar_section} />
        <BriefSection title="E-mails" content={result.email_section} />
        <BriefSection title="Web" content={result.web_section} />
        <BriefSection title="Scouts" content={result.scout_section} />
      </div>
      <section className="brief-sources">
        <div className="eyebrow">Fontes</div>
        <div className="source-list">{result.source_refs.map((ref) => <code key={ref}>{ref}</code>)}</div>
      </section>
      <div className="brief-actions"><a className="btn ghost" href="#/daily-brief/history">Ver histórico</a></div>
    </div>
  );
}

function BriefSection({ title, content }: { title: string; content: string }) {
  return (
    <section className="brief-card">
      <h2>{title}</h2>
      <p>{content}</p>
    </section>
  );
}
