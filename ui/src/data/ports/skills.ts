/** Skill runtime port: list the real, pinned Skills and run a task with or without one, optionally over up to MAX_FILES workspace files. */
export interface SkillOption {
  id: string;
  name: string;
  source: string;
}

export interface SkillRunRequest {
  task: string;
  /** null = run without a Skill. */
  skillId: string | null;
  /** Paths RELATIVE to the bridge workspace (0..MAX_FILES). The UI never reads files: the backend reads each one through governance. */
  files: string[];
  /** ONE .pdf/.png/.jpg/.jpeg path RELATIVE to the bridge workspace, or null. Its bytes are read by the backend through governance. */
  document: string | null;
}

/** Same limit as the bridge; the bridge enforces it, the UI only avoids offering more rows. */
export const MAX_FILES = 5;

export interface SkillRunFile {
  path: string;
  sha256: string;
  chars: number;
  governedAction: string;
}

export interface SkillRunDocument {
  path: string;
  kind: string;
  bytes: number;
  sha256: string;
  pages: number | null;
  extractedChars: number | null;
  governedAction: string;
}

export interface SkillRunResult {
  response: string;
  skillId: string | null;
  skillName: string | null;
  provider: string;
  model: string;
  requestId: string | null;
  promptTokens: number | null;
  files: SkillRunFile[];
  document: SkillRunDocument | null;
}

export interface WorkspaceInfo {
  /** Folder files can be read from, or null when file context is disabled in the bridge. */
  root: string | null;
}

export type SkillErrorKind =
  | 'offline'
  | 'not_configured'
  | 'unknown_skill'
  | 'invalid'
  | 'upstream'
  | 'timeout'
  | 'read_denied'
  | 'file_not_found'
  | 'file_too_large'
  | 'context_too_large'
  | 'unsupported_document'
  | 'invalid_document'
  | 'document_no_text'
  | 'document_too_large'
  | 'document_support_unavailable'
  | 'file_unreadable'
  | 'workspace_not_configured';

export class SkillRuntimeError extends Error {
  readonly kind: SkillErrorKind;
  /** Which requested file made the bridge abort the whole request (file errors only). */
  readonly path?: string;
  constructor(kind: SkillErrorKind, message: string, path?: string) {
    super(message);
    this.kind = kind;
    this.path = path;
  }
}

export interface SkillRuntimePort {
  listSkills(): Promise<SkillOption[]>;
  getWorkspace(): Promise<WorkspaceInfo>;
  run(req: SkillRunRequest): Promise<SkillRunResult>;
}
