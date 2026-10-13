import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Same-origin path to the loopback Skill bridge (keeps the credential out of the browser and CSP unchanged).
const skillProxy = {
  '/skill-api': { target: 'http://127.0.0.1:8765', rewrite: (p: string) => p.replace(/^\/skill-api/, '') },
};
const googleProxy = {
  '/integrations/google': { target: 'http://127.0.0.1:8766' },
};
const dailyBriefProxy = {
  '/daily-brief': { target: 'http://127.0.0.1:8766' },
};
const scoutsProxy = {
  '/scouts': { target: 'http://127.0.0.1:8766' },
};
const computerProxy = {
  '/computer': { target: 'http://127.0.0.1:8767', ws: true },
};
const iaosGatewayProxy = {
  '/ui': { target: 'http://127.0.0.1:8791' },
};
const openDotsProxy = {
  '/api': { target: 'http://127.0.0.1:4310' },
};

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy: { ...skillProxy, ...googleProxy, ...dailyBriefProxy, ...scoutsProxy, ...computerProxy, ...iaosGatewayProxy, ...openDotsProxy } },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy: { ...skillProxy, ...googleProxy, ...dailyBriefProxy, ...scoutsProxy, ...computerProxy, ...iaosGatewayProxy, ...openDotsProxy } },
  test: { environment: 'node', include: ['tests/**/*.test.ts'] },
});
