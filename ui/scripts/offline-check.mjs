// OFFLINE_STARTUP check: production build served from loopback only; every non-loopback origin is blocked.
// Passes when the 4 routes render, all assets load, and nothing non-loopback was even attempted.
// Prereq: `npm run build` then `npm run preview` (127.0.0.1:4173).
// The Skills screen (IAOS_SKILL_UI_VERTICAL_SLICE) has one same-origin surface, /skill-api/*. Run this check with the Skill bridge
// STOPPED: the shell must still start offline, no other route may touch /skill-api, and the Skills screen must render and report
// 'bridge unavailable' cleanly (its failed /skill-api response is expected, not a regression). No fallback or mock is involved.
import { chromium } from 'playwright-core';
import { writeFileSync, mkdirSync } from 'node:fs';

const BASE = process.env.IAOS_UI_URL ?? 'http://127.0.0.1:4173/';
const LOOPBACK = /^(http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\/|data:|blob:|about:)/;

let browser;
for (const channel of ['chrome', 'msedge']) {
  try { browser = await chromium.launch({ channel }); break; } catch { /* next */ }
}
if (!browser) throw new Error('No installed Chrome/Edge found');

const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const blocked = [];
const failed = [];
const consoleErrors = [];
const bridgeRequests = [];
const expectedBridgeErrors = [];
let currentRoute = 'start';
await ctx.route('**/*', (route) => {
  const u = route.request().url();
  if (LOOPBACK.test(u)) return route.continue();
  blocked.push(u);
  return route.abort('blockedbyclient');
});

const routes = [
  ['home', '#/', 'O que o IAOS'],
  ['authority', '#/authority', 'Permissões e'],
  ['execution', '#/execution/ex-0412', 'Ler relatório trimestral'],
  ['settings-models', '#/settings/models', 'IA / Modelos'],
];
const results = [];
const page = await ctx.newPage();
page.on('requestfailed', (r) => failed.push(`${r.url()} ${r.failure()?.errorText}`));
page.on('pageerror', (e) => consoleErrors.push(String(e)));
page.on('request', (r) => {
  if (new URL(r.url()).pathname.startsWith('/skill-api/')) bridgeRequests.push({ route: currentRoute, path: new URL(r.url()).pathname });
});
page.on('console', (m) => {
  if (m.type() !== 'error') return;
  if (currentRoute === 'skills' && (m.location().url ?? '').includes('/skill-api/')) return expectedBridgeErrors.push(m.text());
  consoleErrors.push(m.text());
});
for (const [name, hash, expect] of routes) {
  currentRoute = name;
  await page.goto(BASE + hash);
  await page.reload();
  await page.waitForSelector('.canvas');
  const h1 = (await page.locator('h1').first().innerText()).replace(/\s+/g, ' ');
  const sidebar = await page.locator('aside .brand').count();
  results.push({ route: name, h1, ok: h1.includes(expect) && sidebar === 1 });
}
// Skills screen with the bridge down: renders, offers 'Nenhuma Skill', explains the bridge is unavailable, cannot run.
currentRoute = 'skills';
await page.goto(BASE + '#/skills');
await page.reload();
await page.waitForSelector('.canvas');
await page.waitForSelector('.note.warnnote', { timeout: 8000 }).catch(() => {});
const skillsHeading = (await page.locator('h2').first().innerText()).replace(/\s+/g, ' ');
const noSkillOption = await page.locator('select option', { hasText: 'Nenhuma Skill' }).count();
const bridgeNotice = await page.locator('.note.warnnote').count();
const runDisabled = await page.locator('button[type=submit]').isDisabled();
const skillsOk = skillsHeading.includes('Escolha uma Skill') && noSkillOption === 1 && bridgeNotice === 1 && runDisabled;
results.push({ route: 'skills (bridge stopped)', h1: skillsHeading, ok: skillsOk });
const bridgeOutsideSkills = bridgeRequests.filter((r) => r.route !== 'skills');
await browser.close();

const pass = results.every((r) => r.ok) && !blocked.length && !failed.length && !consoleErrors.length && !bridgeOutsideSkills.length;
const report = { OFFLINE_STARTUP: pass ? 'PASS' : 'FAIL', results, blockedNonLoopback: blocked, failedRequests: failed, consoleErrors,
  SKILL_BRIDGE_UNAVAILABLE_HANDLED: skillsOk ? 'PASS' : 'FAIL', bridgeRequestsOutsideSkillsRoute: bridgeOutsideSkills, expectedBridgeErrors: expectedBridgeErrors.length };
mkdirSync(new URL('../shots/', import.meta.url), { recursive: true });
writeFileSync(new URL('../shots/offline-report.json', import.meta.url), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
process.exit(pass ? 0 : 1);
