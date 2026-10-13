import { dataSource } from '../../data';
import { useData } from '../../app/useData';
import { IconChevron, IconShield } from '../../components/Icons';
import { TopBar } from '../../components/TopBar';

export function AuthorityScreen() {
  const a = useData(() => dataSource.getAuthority(), []);
  return (
    <>
      <TopBar title="Autoridade & Capacidades" subtitle="Configuração de política" />
      <div className="canvas">
        <div className="eyebrow" style={{ marginBottom: 12 }}>Autoridade</div>
        <h1>
          Permissões e
          <br />
          capacidades
        </h1>
        <p className="lede">
          Aqui você edita a política administrativa. A autorização de cada execução continua sendo decidida pela Governance.
        </p>
        {a && (
          <div className="cols">
            <div className="caps">
              {a.capabilities.map((c) => (
                <button key={c.id} className={`cap${c.id === a.selectedId ? ' on' : ''}`}>
                  {c.name}
                  <small>{c.actionCount}</small>
                </button>
              ))}
            </div>
            <div>
              <h2>{a.selectedName}</h2>
              <div className="note">
                <IconShield />
                <div>
                  <b>Política configurada, não autorização concedida.</b> “Permitir automaticamente” define o que a
                  Governance pode aprovar sem perguntar. Nenhuma ação é executada por esta tela.
                </div>
              </div>
              {a.records && (
                <>
                  <h2 style={{ marginTop: 28 }}>Registro real</h2>
                  {a.records.length === 0 ? <div className="empty">Nenhuma invocation registrada.</div> : (
                    <table className="pol">
                      <thead><tr><th>Tool</th><th>Effect</th><th>Status</th><th>Consumo</th><th>Invocation</th></tr></thead>
                      <tbody>{a.records.map((r) => (
                        <tr key={r.invocationId}>
                          <td className="act">{r.toolName}</td><td>{r.effectClass}</td><td>{r.status}</td><td>{r.consumption}</td><td className="mono">{r.invocationId}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  )}
                </>
              )}
              <table className="pol">
                <thead>
                  <tr>
                    <th style={{ width: 120 }}>Ação</th>
                    <th style={{ width: 230 }}>Política configurada</th>
                    <th>Cobertura de auditoria (da capacidade)</th>
                  </tr>
                </thead>
                <tbody>
                  {a.policies.map((p) => (
                    <tr key={p.action}>
                      <td className="act">{p.action}</td>
                      <td>
                        <span className={`sel${p.policyKind === 'never' ? ' never' : ''}`}>
                          {p.policyLabel} <IconChevron />
                        </span>
                      </td>
                      <td>
                        <span className={`pill ${p.coverage.kind === 'validated' ? 'cov' : 'nocov'}`}>{p.coverage.label}</span>
                        <span className="sub">{p.coverage.note}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="scope">
                <span className="k">Escopo da política</span>
                <span className="mono">{a.scopePath}</span>
                <span className="k">Última alteração</span>
                <span>{a.lastChange}</span>
              </div>
              <div className="eyebrow" style={{ marginTop: 30 }}>Últimos 30 dias · registro de execuções</div>
              <div className="stat">
                {a.stats.map((s) => (
                  <div key={s.label} className={s.tone === 'escape' ? 'esc' : undefined}>
                    <strong>{s.value}</strong>
                    <small>{s.href ? <a href={s.href}>{s.label}</a> : s.label}</small>
                  </div>
                ))}
              </div>
              <p className="foot">
                Contagens vêm do registro de execuções e do relatório de auditoria. A interface não as calcula. Cobertura
                validada indica que existe métrica para o escopo; não significa que uma execução foi verificada.
              </p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
