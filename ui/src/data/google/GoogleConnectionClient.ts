import type { GoogleConnectionVM } from '../../contracts/viewmodels';

const BASE = '/integrations/google';

function readState(body: unknown): GoogleConnectionVM {
  if (!body || typeof body !== 'object') throw new Error('invalid_google_connection_state');
  const value = body as Record<string, unknown>;
  if (typeof value.connected !== 'boolean' || (value.email !== null && typeof value.email !== 'string')) {
    throw new Error('invalid_google_connection_state');
  }
  return {
    connected: value.connected,
    email: value.email as string | null,
    calendar: value.calendar === true,
    gmail: value.gmail === true,
    ...(typeof value.errorCode === 'string' ? { errorCode: value.errorCode } : {}),
  };
}

async function request(path: string, init?: RequestInit): Promise<GoogleConnectionVM> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { accept: 'application/json', ...(init?.headers ?? {}) },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const code = body && typeof body === 'object' && typeof (body as Record<string, unknown>).errorCode === 'string'
      ? (body as Record<string, string>).errorCode
      : 'google_connection_request_failed';
    throw new Error(code);
  }
  return readState(body);
}

export const googleConnectionClient = {
  status: () => request(''),
  beginConnect: async (): Promise<string> => {
    const response = await fetch(`${BASE}/connect`, { method: 'POST', headers: { accept: 'application/json' } });
    const body = await response.json().catch(() => null) as Record<string, unknown> | null;
    if (!response.ok || typeof body?.authorizationUrl !== 'string') {
      throw new Error(typeof body?.errorCode === 'string' ? body.errorCode : 'google_connection_request_failed');
    }
    return body.authorizationUrl;
  },
  beginGmail: async (): Promise<string> => {
    const response = await fetch(`${BASE}/capabilities/gmail`, { method: 'POST', headers: { accept: 'application/json' } });
    const body = await response.json().catch(() => null) as Record<string, unknown> | null;
    if (!response.ok || typeof body?.authorizationUrl !== 'string') {
      throw new Error(typeof body?.errorCode === 'string' ? body.errorCode : 'google_connection_request_failed');
    }
    return body.authorizationUrl;
  },
  disconnect: () => request('/disconnect', { method: 'POST' }),
};
