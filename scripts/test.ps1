$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
python -m pytest -q
python -m compileall -q application assurance audit claims cognitive evidence governance scripts skilleval skills
Set-Location (Join-Path $Root 'ui')
npm test
npm run typecheck
npm run build
Set-Location (Join-Path $Root 'opendots')
npm test -- --testTimeout 20000
npm run typecheck
npm run build
