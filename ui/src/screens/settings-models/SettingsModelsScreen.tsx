import { useModels } from '../../app/ModelContext';
import type { CheckResult, ModelLocation, ModelProfileVM } from '../../contracts/viewmodels';
import { Disclosure } from '../../components/Disclosure';
import { LOCATION_LABEL } from '../../components/modelCopy';
import { TopBar } from '../../components/TopBar';

type CheckKey = 'endpoint' | 'creds' | 'model';
type Shown = CheckResult | 'idle' | 'run';

const CHECKS: { key: CheckKey; label: string; breakLabel: string; text: Partial<Record<CheckResult, string>> }[] = [
  { key: 'endpoint', label: 'Endpoint alcançável', breakLabel: 'endpoint inalcançável', text: { ok: 'Responde em /v1', fail: 'Não respondeu' } },
  { key: 'creds', label: 'Credencial aceita', breakLabel: 'credencial recusada', text: { ok: 'Aceita pelo endpoint', fail: 'Recusada', na: 'Não requerida neste endpoint' } },
  { key: 'model', label: 'Modelo disponível', breakLabel: 'modelo indisponível', text: { ok: 'Encontrado no endpoint', fail: 'Não encontrado no endpoint' } },
];
const ICON: Record<Shown, string> = { ok: '✓', na: '–', fail: '▲', idle: '○', run: '…' };
const LOCATIONS: ModelLocation[] = ['LOCAL', 'LAN', 'CLOUD'];
const dot = (p: ModelProfileVM) => `dot-${p.location.toLowerCase()}`;

function SummaryPill({ p, testing }: { p: ModelProfileVM; testing: boolean }) {
  if (testing)
    return (
      <span className="pill st-idle">
        <i />
        Testando…
      </span>
    );
  if (!p.checks)
    return (
      <span className="pill st-idle">
        <i />
        Configurado, não testado
      </span>
    );
  const bad = CHECKS.find((c) => p.checks![c.key] === 'fail');
  return bad ? (
    <span className="pill st-warn">
      <span className="ic">▲</span>Atenção: {bad.breakLabel}
    </span>
  ) : (
    <span className="pill st-ok">
      <span className="ic">✓</span>Pronto para uso
    </span>
  );
}

export function SettingsModelsScreen() {
  const m = useModels();
  const p = m.profiles.find((x) => x.id === m.selectedId);
  return (
    <>
      <TopBar title="Configurações" subtitle="IA / Modelos" showModel />
      <div className="canvas">
        <div className="eyebrow" style={{ marginBottom: 12 }}>Configurações</div>
        <h1>IA / Modelos</h1>
        <p className="lede">
          Escolha onde a cognição roda. O modelo interpreta e propõe; autorização e execução continuam com a Governance,
          qualquer que seja o modelo.
        </p>
        <div className="settings-switcher">
          <a className="active" href="#/settings/models">IA / Modelos</a>
          <a href="#/settings/integrations/google">Integrações / Google</a>
        </div>
        {p && <Body p={p} />}
      </div>
    </>
  );
}

function Body({ p }: { p: ModelProfileVM }) {
  const m = useModels();
  const testing = m.testingId === p.id;
  const isActive = m.activeId === p.id;
  const dirty = m.dirtyIds.has(p.id);
  return (
    <div className="cols">
      <div>
        <div className="caps">
          {m.profiles.map((x) => (
            <button key={x.id} className={`cap${x.id === p.id ? ' on' : ''}`} onClick={() => m.select(x.id)}>
              <span style={{ display: 'flex', flexDirection: 'column', gap: 3, alignItems: 'flex-start' }}>
                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <i className={dot(x)} />
                  {x.name}
                </span>
                <small>
                  {x.model} · {LOCATION_LABEL[x.location]}
                  {x.id === m.activeId ? ' · ativo' : ''}
                </small>
              </span>
            </button>
          ))}
          <button className="cap" style={{ color: 'var(--muted)', fontWeight: 500 }} type="button">
            + Novo perfil
          </button>
        </div>
      </div>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 20 }}>
          <h2>{p.name}</h2>
          <div>
            <SummaryPill p={p} testing={testing} />
          </div>
        </div>
        <div className="three">
          <div><span className="k">Modelo</span><b>{p.model || '—'}</b></div>
          <div><span className="k">Runtime</span><b>{p.runtimeName}</b></div>
          <div><span className="k">Transporte</span><b>OpenAI-compatible</b></div>
        </div>
        <div className="checks">
          {CHECKS.map((c) => {
            const v: Shown = testing ? 'run' : p.checks ? p.checks[c.key] : 'idle';
            const tx = v === 'idle' ? 'Não testado' : v === 'run' ? 'Verificando…' : (c.text[v] ?? '');
            return (
              <div key={c.key} className={`chk ${v}`}>
                <span className="ic">{ICON[v]}</span>
                <b>{c.label}</b>
                <span>{tx}</span>
              </div>
            );
          })}
        </div>
        <Disclosure location={p.location} variant="note" />
        <div className="form">
          <label>
            <span>Nome do perfil</span>
            <input value={p.name} onChange={(e) => m.edit(p.id, { name: e.target.value })} />
          </label>
          <label>
            <span>Runtime</span>
            <input value={p.runtimeName} onChange={(e) => m.edit(p.id, { runtimeName: e.target.value })} />
          </label>
          <div className="row">
            <span>Localidade</span>
            <div className="seg">
              {LOCATIONS.map((l) => (
                <button key={l} className={p.location === l ? 'on' : undefined} onClick={() => m.setLocation(p.id, l)}>
                  {LOCATION_LABEL[l]}
                </button>
              ))}
            </div>
          </div>
          <label>
            <span>Base URL</span>
            <input className="mono" value={p.baseUrl} onChange={(e) => m.edit(p.id, { baseUrl: e.target.value })} />
          </label>
          <label>
            <span>Modelo</span>
            <input value={p.model} onChange={(e) => m.edit(p.id, { model: e.target.value })} />
          </label>
          <div className="row" style={{ alignItems: 'start' }}>
            <span>Credencial</span>
            <div>
              {!p.apiKeyRef ? (
                <div className="keyline">
                  <b>Não requerida</b>
                  <span className="hint">Endpoints locais podem dispensar chave, conforme o runtime.</span>
                </div>
              ) : (
                <>
                  <div className="keyline">
                    <span className="pill st-ok"><span className="ic">●</span>Credencial configurada</span>
                    <span className="mono">{p.apiKeyRef}</span>
                  </div>
                  <div className="keyline" style={{ marginTop: 10 }}>
                    <button className="btn ghost" type="button">Substituir credencial</button>
                    <button className="btn ghost" type="button">Remover</button>
                  </div>
                  <span className="hint">A chave nunca volta para a tela, nem mascarada. O perfil guarda só a referência.</span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="actions">
          <button className="btn" disabled={testing} onClick={() => m.test(p.id)}>Testar conexão</button>
          <button className="btn primary" disabled={!dirty} onClick={() => m.save(p.id)}>Salvar alterações</button>
          <button className="btn ghost" disabled={isActive} onClick={() => m.setActive(p.id)}>
            {isActive ? 'Modelo ativo' : 'Definir como ativo'}
          </button>
        </div>
        <p className="foot">
          As verificações são simuladas: no produto real, endpoint, credencial e modelo são testados separadamente.
          Configurado não é o mesmo que conectado, e conectado não garante que o modelo exista. Sem fallback automático: a
          escolha entre local e remoto é sempre explícita. Esta tela configura cognição; não concede autoridade de
          execução.
        </p>
      </div>
    </div>
  );
}
