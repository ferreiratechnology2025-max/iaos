import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { EXECUTIONS } from '../src/data/fixtures/executions';
import { FixtureDataSource } from '../src/data/fixtures/FixtureDataSource';

const ROOT = join(__dirname, '..');
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
const SRC = walk(join(ROOT, 'src')).filter((f) => /\.(ts|tsx|css)$/.test(f));
const CODE = SRC.filter((f) => /\.(ts|tsx)$/.test(f));
const text = (f: string) => readFileSync(f, 'utf8');
const rel = (f: string) => f.slice(ROOT.length + 1).replace(/\\/g, '/');

describe('IAOS_REAL_UI_SHELL_GATE: static checks', () => {
  // NETWORK_CALLS_ZERO=SUPERSEDED. The first shell was fully offline (NETWORK_CALLS=ZERO / BACKEND_CALLS=ZERO). The product
  // deliberately gained narrow, same-origin functional surfaces. The boundary is not relaxed;
  // it is restated explicitly:
  //   DIRECT_EXTERNAL_NETWORK_CALLS=ZERO   BROWSER_PROVIDER_CALLS=ZERO   BROWSER_SECRET_ACCESS=ZERO
  //   UI_NETWORK_SURFACE=SAME_ORIGIN_ONLY   LOCAL_BRIDGE_BIND=127.0.0.1   AGNES_API_KEY_IN_BROWSER=NO
  const NETWORK_CLIENTS = ['src/components/ComputerPanel.tsx', 'src/components/OpenDotsComposer.tsx', 'src/data/computer/ComputerRuntimeClient.ts', 'src/data/daily-brief/DailyBriefClient.ts', 'src/data/google/GoogleConnectionClient.ts', 'src/data/real/RealIaosDataSource.ts', 'src/data/scouts/ScoutClient.ts', 'src/data/skills/HttpSkillRuntime.ts'];
  const NETWORK_API = /\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|navigator\.sendBeacon|importScripts)\b/;

  it('UI_NETWORK_SURFACE: functional clients touch only same-origin IAOS bridges', () => {
    const users = CODE.filter((f) => NETWORK_API.test(text(f))).map(rel);
    expect(new Set(users)).toEqual(new Set(NETWORK_CLIENTS));
    const t = text(join(ROOT, 'src/data/skills/HttpSkillRuntime.ts'));
    expect(t).not.toMatch(/XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts/);
    expect(t).toMatch(/const BASE = '\/skill-api'/);
    const calls = [...t.matchAll(/fetch\(([^,)]*)/g)].map((m) => m[1].trim());
    expect(calls.length).toBeGreaterThan(0);
    for (const c of calls) expect(c).toMatch(/^`\$\{BASE\}\/[a-z]+`$/);
    const google = text(join(ROOT, 'src/data/google/GoogleConnectionClient.ts'));
    expect(google).toMatch(/fetch\(`/);
    expect(google).toMatch(/const BASE = '\/integrations\/google'/);
    expect(google).not.toMatch(/Authorization|Bearer|localStorage|sessionStorage|client_secret|refresh_token|access_token/);
    const computer = text(join(ROOT, 'src/data/computer/ComputerRuntimeClient.ts'));
    expect(computer).toMatch(/fetch\('\/computer\//);
    expect(computer).toMatch(/new WebSocket\(`\$\{location\.protocol/);
    expect(computer).not.toMatch(/Authorization|Bearer|localStorage|sessionStorage|COMPUTER_TOKEN|agent-computer/);
  });

  it('DIRECT_EXTERNAL_NETWORK_CALLS=ZERO: no absolute http(s)/ws URL in the bridge client or any other code', () => {
    const offenders = CODE.filter((f) => /\b(https?|wss?):\/\/(?!localhost|127\.0\.0\.1|www\.w3\.org|provider\.example|192\.168\.x\.x)/i.test(text(f)));
    // provider.example and 192.168.x.x are inert fixture placeholders (never fetched), same allowance as the offline-assets test.
    expect(offenders.map(rel)).toEqual([]);
    expect(text(join(ROOT, 'src/data/skills/HttpSkillRuntime.ts'))).not.toMatch(/\b(https?|wss?):\/\//i);
  });

  it('BROWSER_PROVIDER_CALLS=ZERO / BROWSER_SECRET_ACCESS=ZERO: no provider host, auth header or secret/env/storage access in src', () => {
    const provider = /agnes-ai\.com|apihub|['"]Authorization['"]\s*:|Bearer\s/;
    const secrets = /process\.env|localStorage|sessionStorage|indexedDB|document\.cookie/;
    expect(CODE.filter((f) => provider.test(text(f))).map(rel)).toEqual([]);
    expect(CODE.filter((f) => secrets.test(text(f))).map(rel)).toEqual([]);
    expect(text(join(ROOT, 'src/data/real/RealIaosDataSource.ts'))).not.toMatch(/OPENAI_API_KEY|VITE_OPENAI_API_KEY|Bearer\s/);
  });

  it('SKILL_BRIDGE_PATH_PREFIX=/skill-api/ and LOCAL_BRIDGE_BIND=127.0.0.1:8765: the only dev/preview proxy', () => {
    const cfg = text(join(ROOT, 'vite.config.ts'));
    expect([...cfg.matchAll(/'(\/[a-z/-]+)':\s*\{/g)].map((m) => m[1])).toEqual(['/skill-api', '/integrations/google', '/daily-brief', '/scouts', '/computer', '/ui', '/api']);
    expect([...cfg.matchAll(/target:\s*'([^']+)'/g)].map((m) => m[1])).toEqual(['http://127.0.0.1:8765', 'http://127.0.0.1:8766', 'http://127.0.0.1:8766', 'http://127.0.0.1:8766', 'http://127.0.0.1:8767', 'http://127.0.0.1:8791', 'http://127.0.0.1:4310']);
  });

  it('CSP connect-src is unchanged: self + localhost (dev HMR) only, no remote origin', () => {
    const m = text(join(ROOT, 'index.html')).match(/connect-src ([^"]*)"/);
    expect(m?.[1].trim()).toBe("'self' ws://localhost:* http://localhost:*");
  });

  it('OFFLINE_UI_ASSETS / REMOTE_FONT_DEPENDENCY=NO: no remote URLs in src or index.html', () => {
    const remote = /(https?:)?\/\/(?!localhost|127\.0\.0\.1|www\.w3\.org)[a-z0-9.-]+\.[a-z]{2,}/i;
    const bad = [...SRC, join(ROOT, 'index.html')].filter((f) => {
      // provider.example is an inert fixture placeholder (RFC 2606 style), not fetched.
      const t = text(f).replace(/https:\/\/provider\.example\/v1/g, '');
      return remote.test(t) || /@import|@font-face|fonts\.googleapis/.test(t);
    });
    expect(bad.map(rel)).toEqual([]);
  });

  it('REAL_SECRETS=ZERO: no key-like tokens in src', () => {
    const keyLike = /\b(sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{8,}|eyJ[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{10,}|[A-Fa-f0-9]{32,})\b/;
    expect(CODE.filter((f) => keyLike.test(text(f))).map(rel)).toEqual([]);
  });

  it('credentials are references only (credential://models/...) or empty', async () => {
    const { profiles } = await new FixtureDataSource().getModelSettings();
    for (const p of profiles) expect(p.apiKeyRef === '' || /^credential:\/\/models\/[a-z0-9-]+$/.test(p.apiKeyRef)).toBe(true);
  });

  it('FIXTURE_DATA_ONLY: only the data layer touches fixtures', () => {
    const offenders = CODE.filter((f) => !rel(f).startsWith('src/data/') && /data\/fixtures|FixtureDataSource/.test(text(f)));
    expect(offenders.map(rel)).toEqual([]);
  });

  it('PRODUCTION_DATA_SOURCE=REAL_IAOS and fixtures require explicit demo mode', () => {
    const source = text(join(ROOT, 'src/data/index.ts'));
    expect(source).toMatch(/VITE_IAOS_DEMO_MODE === 'true'/);
    expect(source).toMatch(/new RealIaosDataSource\(\)/);
    expect(source).toMatch(/new FixtureDataSource\(\)/);
  });

  it('COMPOSER_DOES_NOT_PRETEND_TO_BE_AN_IAOS_AGENT', () => {
    const composer = text(join(ROOT, 'src/components/Composer.tsx'));
    expect(text(join(ROOT, 'src/components/OpenDotsComposer.tsx'))).toMatch(/CopilotKitProvider/);
    expect(composer).not.toMatch(/Mensagem para o IAOS/);
  });

  it('UI_DOES_NOT_COMPUTE_AUDIT_STATE: screens/components never import audit logic or Evidence', () => {
    const ui = CODE.filter((f) => /^src\/(screens|components|app)\//.test(rel(f)));
    const bad = ui.filter((f) => /from ['"][^'"]*(iaos|audit|evidence|governance)[^'"]*['"]/i.test(text(f)));
    expect(bad.map(rel)).toEqual([]);
  });

  it('D:\\iaos is not referenced (no imports, no paths into the governance repo)', () => {
    const bad = CODE.filter((f) => /D:\\iaos(?!-ui)|\.\.\/\.\.\/iaos\b/i.test(text(f)));
    expect(bad.map(rel)).toEqual([]);
  });
});

describe('IAOS_REAL_UI_SHELL_GATE: view-model contract', () => {
  it('every golden audit state is supplied by fixtures (not derived)', () => {
    expect(new Set(EXECUTIONS.map((e) => e.state))).toEqual(
      new Set(['escape', 'verified', 'pending', 'insufficient', 'unsupported']),
    );
  });

  it('Claim / Evidence / Verification are separate objects on every execution', () => {
    for (const e of EXECUTIONS) {
      expect(e.claim).toBeTruthy();
      expect(e.evidence).toBeTruthy();
      expect(e.verification).toBeTruthy();
    }
  });

  it('"Verified" is always paired with a scope', () => {
    for (const e of EXECUTIONS.filter((x) => x.state === 'verified')) expect(e.verification.scope).toBeTruthy();
  });

  it('model profiles cover LOCAL/LAN/CLOUD and keep runtime separate from model', async () => {
    const { profiles } = await new FixtureDataSource().getModelSettings();
    expect(new Set(profiles.map((p) => p.location))).toEqual(new Set(['LOCAL', 'LAN', 'CLOUD']));
    for (const p of profiles) {
      expect(p.transport).toBe('OPENAI_COMPATIBLE');
      expect(p.runtimeName).not.toBe(p.model);
    }
    const atomic = profiles.find((p) => p.runtimeName === 'Atomic Chat')!;
    expect(atomic.model).toBe('Qwen 3');
  });
});
