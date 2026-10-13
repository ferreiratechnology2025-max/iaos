import { useEffect, useMemo, useState } from 'react';
import { CopilotKitProvider, useAgent, useCopilotKit } from '@copilotkit/react-core/v2';

type Dot = { id: string; name: string };
type Workspace = { dots?: Dot[] };

const runtimeUrl = '/api/copilotkit';

function HostedComposer() {
  const [dot, setDot] = useState<Dot | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);
  const { copilotkit } = useCopilotKit();
  const agent = useAgent({
    agentId: `iaos-ui-${threadId ?? 'pending'}`,
    runtimeAgentId: dot?.id ?? 'pending',
    threadId: threadId ?? 'pending',
  });
  const assistantText = useMemo(() => agent.agent.messages
    .filter((message) => message.role === 'assistant')
    .map((message) => typeof message.content === 'string' ? message.content : '')
    .filter(Boolean)
    .at(-1) ?? '', [agent.agent.messages]);

  useEffect(() => {
    let active = true;
    void fetch('/api/workspace', { headers: { Accept: 'application/json' } })
      .then((response) => response.ok ? response.json() as Promise<Workspace> : Promise.reject(new Error(`opendots_${response.status}`)))
      .then(async (workspace) => {
        const selected = workspace.dots?.[0];
        if (!active || !selected) throw new Error('opendots_dot_unavailable');
        const response = await fetch('/api/conversations', {
          method: 'POST', headers: { 'content-type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ dotId: selected.id, title: 'IAOS UI session' }),
        });
        if (!response.ok) throw new Error(`opendots_conversation_${response.status}`);
        const conversation = await response.json() as { id?: string };
        if (!conversation.id) throw new Error('opendots_conversation_invalid');
        if (active) { setDot(selected); setThreadId(conversation.id); }
      })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : 'OpenDots indisponível'); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!agent.isReady) return;
    void copilotkit.connectAgent({ agent: agent.agent }).catch((cause) => setError(cause instanceof Error ? cause.message : 'OpenDots indisponível'));
  }, [agent.isReady, agent.agent, copilotkit]);

  const send = async () => {
    if (!draft.trim() || !threadId || !dot || running || !agent.isReady) return;
    setError(''); setRunning(true);
    agent.agent.addMessage({ id: crypto.randomUUID(), role: 'user', content: draft.trim() });
    setDraft('');
    try { await copilotkit.runAgent({ agent: agent.agent }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'OpenDots turn failed'); }
    finally { setRunning(false); }
  };

  return <div className="composer">
    <div className="box">
      {dot ? `Mensagem para ${dot.name}` : 'Conectando ao OpenDots…'}
      <button type="button" className="send" aria-label="Enviar" disabled={!draft.trim() || running || !threadId} onClick={() => void send()}>↗</button>
    </div>
    <textarea aria-label="Mensagem para OpenDots" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder={dot ? `Fale com ${dot.name}…` : 'OpenDots indisponível'} />
    {assistantText && <div className="composer-response">{assistantText}</div>}
    {error && <div className="composer-error" role="alert">{error}</div>}
    <div className="ctl"><span>{running ? 'OpenDots processando…' : 'OpenDots AgentLoop'}</span><span className="sp">Enter para enviar · Shift+Enter para nova linha</span></div>
  </div>;
}

export function OpenDotsComposer() {
  return <CopilotKitProvider runtimeUrl={runtimeUrl}>
    <HostedComposer />
  </CopilotKitProvider>;
}
