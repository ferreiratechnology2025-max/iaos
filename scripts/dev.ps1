param(
  [switch]$SkipOpenBot,
  [switch]$SkipOpenDots,
  [switch]$SkipUi
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Import-LocalEnv([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  foreach ($line in Get-Content -LiteralPath $Path) {
    if ($line -match '^\s*#' -or $line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
  }
}

Import-LocalEnv (Join-Path $Root '.env')
$runtimeHost = '127.0.0.1'
$runtimePort = [int]($env:COMPUTER_SUPERVISOR_PORT ?? '55248')
$runtimeReady = Test-NetConnection -ComputerName $runtimeHost -Port $runtimePort -InformationLevel Quiet -WarningAction SilentlyContinue
if ($runtimeReady) {
  Write-Host "OPENBOT_RUNTIME=READY ($runtimeHost`:$runtimePort)"
} else {
  Write-Warning "OPENBOT_RUNTIME=NOT_RUNNING; start the documented OpenBot Docker runtime before using agent computer actions."
}
$jobs = @()
$jobs += Start-Job -Name 'iaos-gateway' -ScriptBlock { param($r) Set-Location $r; python scripts/iaos_tool_gateway.py } -ArgumentList $Root
if (-not $SkipOpenDots) { $jobs += Start-Job -Name 'opendots' -ScriptBlock { param($r) Set-Location $r; npm run dev } -ArgumentList (Join-Path $Root 'opendots') }
if (-not $SkipUi) { $jobs += Start-Job -Name 'iaos-ui' -ScriptBlock { param($r) Set-Location $r; npm run dev } -ArgumentList (Join-Path $Root 'ui') }
if (-not $SkipOpenBot) {
  Write-Warning 'OpenBot is imported, but its Docker-managed computer runtime is not started by this script. Start the required OpenBot runtime separately after configuring its local environment.'
}

Write-Host 'IAOS development services started. Press Ctrl+C to stop.'
try { while ($true) { Receive-Job -Job $jobs -Keep; Start-Sleep -Seconds 1 } }
finally { $jobs | Stop-Job -ErrorAction SilentlyContinue; $jobs | Remove-Job -Force -ErrorAction SilentlyContinue }
