# IAOS UI

Real UI shell for IAOS (4 surfaces: Início, Autoridade & Capacidades, Detalhe de execução + Auditoria, Configurações → IA / Modelos).
Product UI located inside the `D:\iaos` repository. Provenance for the imported
snapshot is recorded in `ORIGIN.md`.

## Architecture
UI → view models (`src/contracts`) → data port (`src/data/ports`) → real IAOS
read-only gateway by default. Fixtures are available only with the explicit
`VITE_IAOS_DEMO_MODE=true` setting.
Screens never read a backend, never interpret Evidence, never compute AuditReport state.

## Scripts
- `npm run dev` · `npm run build` · `npm run preview`
- `npm run typecheck` · `npm test` (gate static checks)
- `npm run shoot` (needs `npm run preview` on 127.0.0.1:4173; writes `shots/*.png` + network report)

## Constraints
No network calls, no remote fonts (local font stack), mock credentials are `credential://models/*` references only.
The IAOS gateway exposes read-only `/ui/status`, `/ui/invocations`,
`/ui/authorizations`, `/ui/consumptions`, `/ui/receipts`, and `/ui/evidence`
views over the existing durable SQLite store. The UI does not open SQLite and
does not write governance state. Model ownership remains with the OpenDots
host; the browser receives status only, never an API key.

The model connection test is intentionally read-only and unavailable until a
governed administrative operation is defined. Golden references:
IAOS_PRODUCT_UI_SPEC v0.2 screenshots.
Note: the CSP `connect-src` allows localhost only for the Vite dev server (HMR).

DEV_CSP_ALLOWS_LOCALHOST=EXPECTED
PRODUCTION_CSP_HARDENING=PENDING
OFFLINE_STARTUP: `npm run offline` (production build, non-loopback blocked, 4 routes) -> shots/offline-report.json
