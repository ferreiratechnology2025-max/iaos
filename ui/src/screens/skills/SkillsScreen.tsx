import { useEffect, useState } from 'react';
import { skillRuntime } from '../../data';
import { SkillRuntimeError } from '../../data/ports/skills';
import { MAX_FILES } from '../../data/ports/skills';
import type { SkillOption, SkillRunResult } from '../../data/ports/skills';
import { TopBar } from '../../components/TopBar';

const NONE = '';

type Catalog =
  | { status: 'loading' }
  | { status: 'ready'; skills: SkillOption[]; workspace: string | null }
  | { status: 'error'; error: SkillRuntimeError };
type Phase =
  | { kind: 'idle' }
  | { kind: 'running'; label: string }
  | { kind: 'done'; result: SkillRunResult }
  | { kind: 'error'; error: SkillRuntimeError };

const MESSAGES: Record<string, string> = {
  offline: 'O servidor local de Skills não está respondendo. Inicie-o no repositório do IAOS com `python -m skilleval.manual_server --workspace PASTA` (com AGNES_API_KEY no ambiente) e recarregue.',
  not_configured: 'O servidor local está ativo, mas sem credencial do modelo (AGNES_API_KEY) no ambiente dele.',
  unknown_skill: 'Essa Skill não existe na biblioteca. Escolha uma da lista.',
  invalid: 'A tarefa ou o arquivo não foram aceitos (vazio ou grande demais).',
  upstream: 'O provedor do modelo não respondeu como esperado.',
  timeout: 'O modelo não respondeu dentro do tempo limite.',
  read_denied: 'A governança recusou a leitura deste arquivo (fora do workspace ou não permitido). Nada foi lido e nada foi enviado ao modelo.',
  file_not_found: 'Arquivo não encontrado no workspace. Use um caminho relativo a ele.',
  file_too_large: 'O arquivo é grande demais para enviar ao modelo nesta versão.',
  unsupported_document: 'Tipo de documento não suportado. Use .pdf, .png, .jpg ou .jpeg. Nada foi lido.',
  invalid_document: 'O arquivo não é um PDF ou imagem válido (ou o PDF está protegido por senha). Nada foi enviado ao modelo.',
  document_no_text: 'Este PDF não tem texto extraível (provavelmente é digitalizado). Esta versão não faz OCR; nada foi enviado ao modelo.',
  document_too_large: 'O documento é grande demais para esta versão (limite de 10 MB). Nada foi enviado ao modelo.',
  document_support_unavailable: 'O servidor local não tem suporte a PDF instalado (pip install pypdf).',
  context_too_large: 'A soma do conteúdo (arquivos e texto do PDF) passa do limite de contexto enviado ao modelo (100 mil caracteres). Remova ou troque algum arquivo. Nada foi enviado ao modelo.',
  file_unreadable: 'O arquivo foi autorizado, mas não pôde ser lido como texto UTF-8.',
  workspace_not_configured: 'O servidor local foi iniciado sem workspace, então arquivos estão desabilitados.',
};

/** File errors abort the whole request; say which file caused it and that no partial context was sent. */
function describe(e: SkillRuntimeError): string {
  const base = MESSAGES[e.kind];
  return e.path ? `${base} Arquivo: ${e.path}. A requisição inteira foi cancelada, sem contexto parcial.` : base;
}

function asError(e: unknown): SkillRuntimeError {
  return e instanceof SkillRuntimeError ? e : new SkillRuntimeError('offline', String(e));
}

export function SkillsScreen() {
  const [catalog, setCatalog] = useState<Catalog>({ status: 'loading' });
  const [skillId, setSkillId] = useState(NONE);
  const [files, setFiles] = useState<string[]>(['']);
  const [document, setDocument] = useState('');
  const [task, setTask] = useState('');
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });

  useEffect(() => {
    let alive = true;
    Promise.all([skillRuntime.listSkills(), skillRuntime.getWorkspace()])
      .then(([skills, ws]) => alive && setCatalog({ status: 'ready', skills, workspace: ws.root }))
      .catch((e) => alive && setCatalog({ status: 'error', error: asError(e) }));
    return () => {
      alive = false;
    };
  }, []);

  const skills = catalog.status === 'ready' ? catalog.skills : [];
  const workspace = catalog.status === 'ready' ? catalog.workspace : null;
  const selected = skills.find((s) => s.id === skillId);
  const running = phase.kind === 'running';
  const canRun = catalog.status === 'ready' && task.trim().length > 0 && !running;

  const setFileAt = (i: number, v: string) => setFiles((fs) => fs.map((f, j) => (j === i ? v : f)));
  const removeFileAt = (i: number) => setFiles((fs) => (fs.length > 1 ? fs.filter((_, j) => j !== i) : ['']));
  const chosen = files.map((f) => f.trim()).filter((f) => f !== '');

  const execute = () => {
    if (!canRun) return;
    setPhase({ kind: 'running', label: selected?.name ?? 'Sem Skill' });
    skillRuntime
      .run({ task, skillId: skillId === NONE ? null : skillId, files: chosen, document: document.trim() === '' ? null : document.trim() })
      .then((result) => setPhase({ kind: 'done', result }))
      .catch((e) => setPhase({ kind: 'error', error: asError(e) }));
  };

  return (
    <>
      <TopBar title="Skills" subtitle="Equipe o modelo com uma Skill real e, se quiser, até 5 arquivos seus" simulated={false} />
      <div className="canvas">
        <div className="eyebrow" style={{ marginBottom: 12 }}>Skills</div>
        <h2>Escolha uma Skill, arquivos e descreva a tarefa</h2>
        <p className="lede">
          A Skill é o texto original do Agency Agents, sem alterações. O arquivo é lido pelo IAOS com autorização de leitura (somente leitura, até {MAX_FILES} arquivos que
          você escolhe, dentro do workspace; se um for recusado, nada é enviado ao modelo) ou um documento (PDF ou imagem). Nenhum dos dois dá ferramentas, capacidades nem autoridade ao modelo.
        </p>

        {catalog.status === 'error' && <div className="note warnnote">{describe(catalog.error)}</div>}

        <form
          className="skillform"
          onSubmit={(e) => {
            e.preventDefault();
            execute();
          }}
        >
          <label className="field">
            <span className="eyebrow">Skill</span>
            <select value={skillId} onChange={(e) => setSkillId(e.target.value)} disabled={running || catalog.status !== 'ready'}>
              <option value={NONE}>Nenhuma Skill</option>
              {skills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <small className="sub mono">{selected ? selected.source : 'Executa a tarefa sem Skill.'}</small>
          </label>
          <div className="field" role="group" aria-labelledby="files-label">
            <span className="eyebrow" id="files-label">Arquivos (opcional, até {MAX_FILES})</span>
            <div className="filerows">
              {files.map((f, i) => (
                <div className="filerow" key={i}>
                  <input
                    type="text"
                    value={f}
                    aria-label={`Arquivo ${i + 1}`}
                    placeholder={i === 0 ? 'src/payment.ts' : 'src/db.ts'}
                    onChange={(e) => setFileAt(i, e.target.value)}
                    disabled={running || workspace === null}
                    spellCheck={false}
                    autoComplete="off"
                  />
                  {files.length > 1 && (
                    <button type="button" className="btn small" aria-label={`Remover arquivo ${i + 1}`} onClick={() => removeFileAt(i)} disabled={running}>
                      Remover
                    </button>
                  )}
                </div>
              ))}
            </div>
            <button
              type="button"
              className="btn small"
              onClick={() => setFiles((fs) => [...fs, ''])}
              disabled={running || workspace === null || files.length >= MAX_FILES}
            >
              + Adicionar arquivo
            </button>
            <small className="sub mono">
              {workspace === null
                ? catalog.status === 'ready'
                  ? 'Arquivos desabilitados: o servidor local foi iniciado sem --workspace.'
                  : 'Aguardando o servidor local…'
                : `Caminhos relativos ao workspace: ${workspace}`}
            </small>
          </div>
          <label className="field">
            <span className="eyebrow">Documento (opcional)</span>
            <input
              type="text"
              value={document}
              placeholder="contrato.pdf"
              onChange={(e) => setDocument(e.target.value)}
              disabled={running || workspace === null}
              spellCheck={false}
              autoComplete="off"
            />
            <small className="sub mono">
              {workspace === null
                ? 'Documentos desabilitados: o servidor local foi iniciado sem --workspace.'
                : 'Um PDF (texto extraído) ou imagem PNG/JPG, caminho relativo ao workspace.'}
            </small>
          </label>
          <label className="field">
            <span className="eyebrow">Tarefa</span>
            <textarea
              rows={7}
              value={task}
              placeholder="Revise este código procurando falhas críticas…"
              onChange={(e) => setTask(e.target.value)}
              disabled={running}
            />
          </label>
          <div className="actions">
            <button className="btn primary" type="submit" disabled={!canRun}>
              {running ? 'Executando…' : 'Executar'}
            </button>
            {running && <span className="sub">Aguardando o modelo ({phase.label}). Pode levar alguns segundos.</span>}
          </div>
        </form>

        {phase.kind === 'error' && <div className="note warnnote" role="alert">{describe(phase.error)}</div>}

        {phase.kind === 'done' && (
          <section className="skillres" aria-live="polite">
            <div className="eyebrow">Resposta</div>
            <h2>{phase.result.skillName ?? 'Sem Skill'}</h2>
            <div className="meta">
              <span>via {phase.result.provider}</span>
              <b>{phase.result.model}</b>
              {phase.result.requestId && <span>pedido <b>{phase.result.requestId}</b></span>}
            </div>
            {phase.result.files.map((f) => (
              <div className="meta" key={f.path}>
                <span>arquivo <b>{f.path}</b></span>
                <span>sha256 <b>{f.sha256.slice(0, 12)}</b></span>
                <span>leitura autorizada <b>{f.governedAction}</b></span>
              </div>
            ))}
            {phase.result.document && (
              <div className="meta">
                <span>documento <b>{phase.result.document.path}</b></span>
                <span>{phase.result.document.kind}</span>
                <span>{phase.result.document.bytes} bytes</span>
                {phase.result.document.pages !== null && <span>{phase.result.document.pages} páginas</span>}
                <span>sha256 <b>{phase.result.document.sha256.slice(0, 12)}</b></span>
                <span>leitura autorizada <b>{phase.result.document.governedAction}</b></span>
              </div>
            )}
            <pre className="out">{phase.result.response.trim()}</pre>
          </section>
        )}
      </div>
    </>
  );
}
