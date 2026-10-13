export interface DailyBriefResultVM {
  run_id: string;
  status: 'success';
  generated_at: string;
  summary: string;
  calendar_section: string;
  email_section: string;
  web_section: string;
  scout_section: string;
  source_refs: string[];
}

export interface DailyBriefRunSummaryVM {
  run_id: string;
  requested_at: string;
  completed_at: string;
  status: 'success' | 'failed';
  error_code: string | null;
  trigger: 'manual' | 'schedule';
  routine_id: string | null;
}

export interface DailyBriefRunDetailVM extends DailyBriefRunSummaryVM {
  summary: string | null;
  calendar_section: string | null;
  email_section: string | null;
  web_section: string | null;
  scout_section: string | null;
  source_refs: string[];
}

export interface DailyBriefRoutineVM {
  routine_id: string;
  frequency: 'daily';
  time: string;
  timezone: string;
  enabled: boolean;
  updated_at: string;
}

const BASE = '/daily-brief';

function readResult(body: unknown): DailyBriefResultVM {
  if (!body || typeof body !== 'object') throw new Error('daily_brief_invalid_result');
  const value = body as Record<string, unknown>;
  const strings = ['run_id', 'generated_at', 'summary', 'calendar_section', 'email_section', 'web_section', 'scout_section'];
  if (strings.some((key) => typeof value[key] !== 'string') || value.status !== 'success' || !Array.isArray(value.source_refs) || value.source_refs.some((ref) => typeof ref !== 'string')) {
    throw new Error('daily_brief_invalid_result');
  }
  return {
    run_id: value.run_id as string,
    status: 'success',
    generated_at: value.generated_at as string,
    summary: value.summary as string,
    calendar_section: value.calendar_section as string,
    email_section: value.email_section as string,
    web_section: value.web_section as string,
    scout_section: value.scout_section as string,
    source_refs: value.source_refs as string[],
  };
}

function readRunSummary(value: unknown): DailyBriefRunSummaryVM {
  if (!value || typeof value !== 'object') throw new Error('daily_brief_invalid_history');
  const item = value as Record<string, unknown>;
  if (typeof item.run_id !== 'string' || typeof item.requested_at !== 'string' || typeof item.completed_at !== 'string' || (item.status !== 'success' && item.status !== 'failed') || (item.error_code !== null && typeof item.error_code !== 'string') || (item.trigger !== 'manual' && item.trigger !== 'schedule') || (item.routine_id !== null && typeof item.routine_id !== 'string')) {
    throw new Error('daily_brief_invalid_history');
  }
  return {
    run_id: item.run_id,
    requested_at: item.requested_at,
    completed_at: item.completed_at,
    status: item.status,
    error_code: item.error_code,
    trigger: item.trigger,
    routine_id: item.routine_id,
  };
}

function readRunDetail(value: unknown): DailyBriefRunDetailVM {
  const summary = readRunSummary(value);
  const item = value as Record<string, unknown>;
  const fields = ['summary', 'calendar_section', 'email_section', 'web_section', 'scout_section'];
  if (fields.some((key) => item[key] !== null && typeof item[key] !== 'string') || !Array.isArray(item.source_refs) || item.source_refs.some((ref) => typeof ref !== 'string')) {
    throw new Error('daily_brief_invalid_history');
  }
  return {
    ...summary,
    summary: item.summary as string | null,
    calendar_section: item.calendar_section as string | null,
    email_section: item.email_section as string | null,
    web_section: item.web_section as string | null,
    scout_section: item.scout_section as string | null,
    source_refs: item.source_refs as string[],
  };
}

function readRoutine(value: unknown): DailyBriefRoutineVM | null {
  if (!value || typeof value !== 'object') throw new Error('daily_brief_invalid_routine');
  const routine = (value as Record<string, unknown>).routine;
  if (routine === null) return null;
  if (!routine || typeof routine !== 'object') throw new Error('daily_brief_invalid_routine');
  const item = routine as Record<string, unknown>;
  if (typeof item.routine_id !== 'string' || item.frequency !== 'daily' || typeof item.time !== 'string' || typeof item.timezone !== 'string' || typeof item.enabled !== 'boolean' || typeof item.updated_at !== 'string') {
    throw new Error('daily_brief_invalid_routine');
  }
  return item as unknown as DailyBriefRoutineVM;
}

function windowForToday() {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return { after: start.toISOString(), before: end.toISOString() };
}

export const dailyBriefClient = {
  run: async (): Promise<DailyBriefResultVM> => {
    const window = windowForToday();
    const response = await fetch(`${BASE}/run`, {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
        calendar_window: window,
        email_window: { after: window.after.slice(0, 10), before: window.before.slice(0, 10) },
        email_limit: 1,
        web_queries: ['artificial intelligence'],
        web_result_limit: 1,
      }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const code = body && typeof body === 'object' && typeof (body as Record<string, unknown>).errorCode === 'string'
        ? (body as Record<string, string>).errorCode
        : 'daily_brief_failed';
      throw new Error(code);
    }
    return readResult(body);
  },
  listHistory: async (): Promise<DailyBriefRunSummaryVM[]> => {
    const response = await fetch(`${BASE}/history`, { headers: { accept: 'application/json' } });
    const body = await response.json().catch(() => null);
    if (!response.ok || !body || typeof body !== 'object' || !Array.isArray((body as Record<string, unknown>).runs)) throw new Error('daily_brief_history_failed');
    return (body as { runs: unknown[] }).runs.map(readRunSummary);
  },
  getHistory: async (runId: string): Promise<DailyBriefRunDetailVM> => {
    const response = await fetch(`${BASE}/history/${encodeURIComponent(runId)}`, { headers: { accept: 'application/json' } });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error('daily_brief_history_failed');
    return readRunDetail(body);
  },
  getRoutine: async (): Promise<DailyBriefRoutineVM | null> => {
    const response = await fetch(`${BASE}/routine`, { headers: { accept: 'application/json' } });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error('daily_brief_routine_failed');
    return readRoutine(body);
  },
  saveRoutine: async (routine: Pick<DailyBriefRoutineVM, 'time' | 'timezone' | 'enabled'>): Promise<DailyBriefRoutineVM> => {
    const response = await fetch(`${BASE}/routine`, {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify(routine),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error('daily_brief_routine_failed');
    const result = readRoutine(body);
    if (!result) throw new Error('daily_brief_invalid_routine');
    return result;
  },
};
