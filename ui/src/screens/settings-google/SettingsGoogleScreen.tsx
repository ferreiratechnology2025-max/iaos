import { useCallback, useEffect, useState } from 'react';
import type { GoogleConnectionVM } from '../../contracts/viewmodels';
import { googleConnectionClient } from '../../data/google/GoogleConnectionClient';
import { TopBar } from '../../components/TopBar';

const EMPTY: GoogleConnectionVM = { connected: false, email: null, calendar: false, gmail: false };

export function SettingsGoogleScreen() {
  const [state, setState] = useState<GoogleConnectionVM | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setState(await googleConnectionClient.status()); setError(null); }
    catch (cause) { setState(EMPTY); setError(cause instanceof Error ? cause.message : 'google_connection_unavailable'); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const run = async (operation: () => Promise<GoogleConnectionVM>) => {
    setBusy(true); setError(null);
    try { setState(await operation()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'google_connection_failed'); }
    finally { setBusy(false); }
  };

  const startConnect = async () => {
    setBusy(true); setError(null);
    try { window.location.assign(await googleConnectionClient.beginConnect()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'google_connection_failed'); setBusy(false); }
  };

  const startGmail = async () => {
    setBusy(true); setError(null);
    try { window.location.assign(await googleConnectionClient.beginGmail()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'google_connection_failed'); setBusy(false); }
  };

  const connected = state?.connected === true;
  return (
    <>
      <TopBar title="Configurações" subtitle="Integrações / Google" showModel simulated={false} />
      <div className="canvas google-settings">
        <div className="eyebrow" style={{ marginBottom: 12 }}>Configurações · Integrações</div>
        <h1>Google</h1>
        <p className="lede">Conecte uma conta Google para usar Calendar e, quando necessário, Gmail.</p>
        <div className="google-card">
          <div className="google-card-head">
            <div><span className="google-mark" aria-hidden="true">G</span><h2>Google</h2></div>
            {connected && <span className="pill st-ok"><span className="ic">✓</span>Conectado</span>}
          </div>
          {!connected ? (
            <div className="google-empty">
              <p>Não conectado</p><span>Escolha sua conta Google em uma única etapa.</span>
              <button className="btn primary" disabled={busy} onClick={() => void startConnect()}>{busy ? 'Abrindo Google…' : 'Conectar Google'}</button>
            </div>
          ) : (
            <>
              <div className="google-identity"><span className="g verified" />{state.email}</div>
              <div className="google-capabilities"><Capability label="Calendar" enabled={state.calendar} /><Capability label="Gmail" enabled={state.gmail} /></div>
              <div className="actions">
                {!state.gmail && <button className="btn" disabled={busy} onClick={() => void startGmail()}>Permitir Gmail</button>}
                <button className="btn ghost" disabled={busy} onClick={() => void run(googleConnectionClient.disconnect)}>Desconectar</button>
              </div>
            </>
          )}
          {error && <div className="google-error" role="status">Não foi possível concluir a operação ({error}).</div>}
        </div>
        <p className="foot">A conexão é gerenciada pelo serviço local. Tokens, segredos, arquivos de credencial e identificadores internos nunca são enviados para esta tela.</p>
      </div>
    </>
  );
}

function Capability({ label, enabled }: { label: string; enabled: boolean }) {
  return <div className="google-capability"><span>{label}</span><b className={enabled ? 'enabled' : ''}>{enabled ? '✓ Autorizado' : 'Não autorizado'}</b></div>;
}
