// Screenshots + network audit of the REAL app (vite preview on 127.0.0.1:4173).
// Uses an installed Chrome/Edge via playwright-core. Writes shots/*.png and shots/report.json.
import { chromium } from 'playwright-core';
import { mkdirSync, writeFileSync } from 'node:fs';

const BASE = process.env.IAOS_UI_URL ?? 'http://127.0.0.1:4173/';
const OUT = new URL('../shots/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
mkdirSync(OUT, { recursive: true });

let browser;
for (const channel of ['chrome', 'msedge']) {
  try { browser = await chromium.launch({ channel }); break; } catch { /* try next */ }
}
if (!browser) throw new Error('No installed Chrome/Edge found');

const requests = [];
const external = [];
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
ctx.on('request', (r) => {
  const u = r.url();
  requests.push(u);
  if (!/^(http:\/\/127\.0\.0\.1:4173|data:|blob:|about:)/.test(u)) external.push(u);
});

const shots = [
  ['home', '#/', null],
  ['home-model-atomic', '#/', async (p) => p.click('#mbtn')],
  ['home-model-cloud', '#/', async (p) => { await p.click('#mbtn'); await p.click('.opt[data-id="cloud"]'); }],
  ['authority', '#/authority', null],
  ['exec-escape', '#/execution/ex-0412', null],
  ['exec-escape-details', '#/execution/ex-0412', async (p) => { for (const d of await p.$$('details.tech')) await d.evaluate((e) => (e.open = true)); }],
  ['exec-verified', '#/execution/ex-0398', null],
  ['exec-pending', '#/execution/ex-0391', null],
  ['exec-insufficient', '#/execution/ex-0377', null],
  ['exec-unsupported', '#/execution/ex-0360', null],
  ['settings-local', '#/settings/models', null],
  ['settings-lan-untested', '#/settings/models', async (p) => p.click('.cap:nth-child(2)')],
  ['settings-lan-connected', '#/settings/models', async (p) => { await p.click('.cap:nth-child(2)'); await p.click('button.btn:has-text("Testar conexão")'); await p.waitForTimeout(1400); }],
  ['settings-cloud-failed', '#/settings/models', async (p) => { await p.click('.cap:nth-child(3)'); await p.click('button.btn:has-text("Testar conexão")'); await p.waitForTimeout(1400); }],
];

for (const [name, hash, act] of shots) {
  const page = await ctx.newPage();
  await page.goto(BASE + hash);
  await page.waitForSelector('.canvas, .top');
  await page.waitForTimeout(250);
  if (act) { await act(page); await page.waitForTimeout(200); }
  await page.screenshot({ path: `${OUT}${name}.png` });
  await page.close();
}
{
  // Full-page capture, comparable to golden/authority.png (which is full-page).
  const page = await ctx.newPage();
  await page.goto(BASE + '#/authority');
  await page.waitForSelector('.canvas');
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${OUT}authority-full.png`, fullPage: true });
  await page.close();
}
await browser.close();

const report = { total: requests.length, external, pass: external.length === 0 };
writeFileSync(`${OUT}report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
