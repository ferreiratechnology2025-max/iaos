import { useEffect, useState } from 'react';

export type Route =
  | { name: 'home' }
  | { name: 'authority' }
  | { name: 'skills' }
  | { name: 'execution'; id: string }
  | { name: 'settings-models' }
  | { name: 'settings-google' }
  | { name: 'daily-brief' }
  | { name: 'daily-brief-history'; runId?: string }
  | { name: 'scouts' };

export const DEFAULT_EXECUTION_ID = 'ex-0412';

export function parseHash(hash: string): Route {
  const [rawPath, rawQuery = ''] = hash.replace(/^#\/?/, '').split('?');
  const path = rawPath;
  const parts = path.split('/').filter(Boolean);
  if (parts[0] === 'authority') return { name: 'authority' };
  if (parts[0] === 'skills') return { name: 'skills' };
  if (parts[0] === 'scouts') return { name: 'scouts' };
  if (parts[0] === 'settings' && parts[1] === 'models') return { name: 'settings-models' };
  if (parts[0] === 'settings' && parts[1] === 'integrations' && parts[2] === 'google') return { name: 'settings-google' };
  if (parts[0] === 'daily-brief' && parts[1] === 'history') {
    const runId = new URLSearchParams(rawQuery).get('run') ?? undefined;
    return { name: 'daily-brief-history', runId };
  }
  if (parts[0] === 'daily-brief') return { name: 'daily-brief' };
  if (parts[0] === 'execution') return { name: 'execution', id: parts[1] ?? DEFAULT_EXECUTION_ID };
  return { name: 'home' };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      setRoute(parseHash(window.location.hash));
      window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}
