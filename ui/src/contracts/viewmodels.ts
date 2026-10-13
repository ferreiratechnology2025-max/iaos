/**
 * View-model contracts: the ONLY shapes screens consume.
 *
 * Rule: screens receive presentation-ready data. They never read a backend,
 * never interpret Evidence, and never compute an AuditReport state. Every
 * state and label below is produced upstream (fixtures today, an IAOS adapter
 * later) and merely rendered here.
 */

/** Presentation state of an audited execution, as reported by the audit layer. */
export type AuditState =
  | 'escape' // silent escape: claimed PASS and verified != PASS
  | 'verified' // verified within a named scope
  | 'fail' // verified failure
  | 'pending' // verification has not run yet
  | 'insufficient' // should be evaluable, but facts are missing
  | 'unsupported'; // no validated metric covers this domain/scope

export type StageState = 'done' | 'on' | 'alert' | 'warn' | 'na' | 'wait';

export interface StageVM {
  /** Stable domain term (Intent, Authorization, ...). */
  name: string;
  state: StageState;
  caption: string;
}

export interface TechRowVM {
  label: string;
  value: string;
  emphasis?: boolean;
}

// ---- shell -----------------------------------------------------------------

export interface RecentExecutionVM {
  id: string;
  title: string;
  state: AuditState;
  stateLabel: string;
  duration: string;
}

export type ModelLocation = 'LOCAL' | 'LAN' | 'CLOUD';

export interface ShellVM {
  workspacePath: string;
  recents: RecentExecutionVM[];
}

// ---- home ------------------------------------------------------------------

export interface HomeStartVM {
  index: string;
  label: string;
  href: string;
}

export interface SummaryItemVM {
  value: string;
  label: string;
  hint?: string;
  tone?: 'default' | 'escape';
  href?: string;
}

export interface HomeVM {
  starts: HomeStartVM[];
  flow: { name: string; caption: string }[];
  summary: SummaryItemVM[];
}

// ---- authority -------------------------------------------------------------

export type PolicyKind = 'auto' | 'ask' | 'never';

export interface PolicyRowVM {
  action: string;
  policyKind: PolicyKind;
  policyLabel: string;
  coverage: { kind: 'validated' | 'none'; label: string; note: string };
}

export interface CapabilityVM {
  id: string;
  name: string;
  actionCount: string;
}

export interface AuthorityVM {
  capabilities: CapabilityVM[];
  selectedId: string;
  selectedName: string;
  policies: PolicyRowVM[];
  scopePath: string;
  lastChange: string;
  stats: { value: string; label: string; tone?: 'escape'; href?: string }[];
  records?: AuthorityRecordVM[];
}

export interface AuthorityRecordVM {
  authorizationId: string;
  invocationId: string;
  executionId: string;
  toolName: string;
  effectClass: string;
  resource: string;
  argumentsDigest: string;
  status: string;
  consumption: string;
  receiptId?: string | null;
  timestamp?: number | null;
}

// ---- execution detail ------------------------------------------------------

export interface ExecutionViewModel {
  id: string;
  executionId: string;
  title: string;
  when: string;
  action: string;
  resource: string;
  state: AuditState;
  stateLabel: string;
  stages: StageVM[];
  claim: {
    pillLabel: string;
    pillKind: 'neutral' | 'pending';
    summary: string;
    tech: TechRowVM[];
  };
  evidence: {
    available: boolean;
    pillLabel?: string;
    pillKind?: 'neutral' | 'insufficient';
    heading: string;
    summary: string;
    caution?: string;
    tech: TechRowVM[];
  };
  verification: {
    heading: string;
    subheading: string;
    pillLabel?: string;
    scope?: string;
    summary: string;
    tech: TechRowVM[];
    footnote?: string;
  };
  silentEscape?: {
    explanation: string;
    claimedLabel: string;
    claimedNote: string;
    verifiedLabel: string;
    causeText: string;
    correlatedTitle: string;
    correlatedNote: string;
    tech: TechRowVM[];
  };
}

// ---- model profiles --------------------------------------------------------

export type CheckResult = 'ok' | 'fail' | 'na';

export interface ModelChecks {
  endpoint: CheckResult;
  creds: CheckResult;
  model: CheckResult;
}

export interface ModelProfileVM {
  id: string;
  name: string;
  transport: 'OPENAI_COMPATIBLE';
  location: ModelLocation;
  runtimeName: string;
  baseUrl: string;
  /** Reference only (credential://...). The secret itself never reaches the UI. */
  apiKeyRef: string;
  model: string;
  enabled: boolean;
  /** null = configured but never tested. */
  checks: ModelChecks | null;
}

export interface ModelSettingsVM {
  profiles: ModelProfileVM[];
  activeId: string;
}

// Sanitized product state. Provider credentials and internal account keys are
// intentionally absent from this browser-facing contract.
export interface GoogleConnectionVM {
  connected: boolean;
  email: string | null;
  calendar: boolean;
  gmail: boolean;
  errorCode?: string;
}
