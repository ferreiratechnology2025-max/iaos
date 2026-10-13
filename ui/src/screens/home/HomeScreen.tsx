import { dataSource } from '../../data';
import { useData } from '../../app/useData';
import { Composer } from '../../components/Composer';
import { FlowStrip } from '../../components/FlowStrip';
import { IconArrow } from '../../components/Icons';
import { TopBar } from '../../components/TopBar';
import { Orbit } from './Orbit';
import { ComputerPanel } from '../../components/ComputerPanel';

export function HomeScreen({ workspacePath }: { workspacePath: string }) {
  const home = useData(() => dataSource.getHome(), []);
  return (
    <>
      <TopBar title="Nova sessão" subtitle={workspacePath} showModel />
      <div className="canvas">
        <div className="hero">
          <Orbit />
          <div>
            <h1>
              O que o IAOS
              <br />
              deve fazer?
            </h1>
            <p className="lede">
              Descreva o resultado. O IAOS pede autorização, executa e mostra o que foi provado, e dentro de qual escopo.
            </p>
            <div className="starts">
              {home?.starts.map((s) => (
                <a key={s.index} className="start" href={s.href}>
                  <span>{s.index}</span>
                  {s.label}
                  <IconArrow />
                </a>
              ))}
            </div>
          </div>
        </div>
        <div className="eyebrow" style={{ marginBottom: 14 }}>
          Fluxo de toda execução
        </div>
        {home && <FlowStrip items={home.flow.map((f, i) => ({ ...f, state: i === 0 ? 'on' : undefined }))} />}
        <div className="summary">
          {home?.summary.map((s) => (
            <div key={s.label} className={s.tone === 'escape' ? 'esc' : undefined}>
              <strong>{s.value}</strong>
              <small>
                {s.href ? <a href={s.href} style={{ textDecoration: 'underline', textUnderlineOffset: 3 }}>{s.label}</a> : s.label}
                {s.hint && (
                  <>
                    <br />
                    <span style={{ color: 'var(--faint)' }}>{s.hint}</span>
                  </>
                )}
              </small>
            </div>
          ))}
        </div>
      </div>
      <Composer />
      <ComputerPanel />
    </>
  );
}
