# Recreates ONLY the standalone takeover-gate computer container and re-applies its egress policy.
# EXPERIMENTAL_STANDALONE_POLICY: allow_all, no rules. Not a production policy.
# The policy lives in memory in the process, so every recreate/restart drops it; this script is
# the bootstrap that puts it back. The token is read from the previous container in memory only
# and is never printed.
param(
  [string]$Name = 'openbot-computer-takeover-gate',
  [string]$Image = 'openbot-agent-computer:latest',
  [int]$HostPort = 55248,
  # Browser-profile volume. A NEW name gives an empty profile (a recreate on the same volume is NOT a reset).
  [string]$ProfileVolume = 'openbot-profile-takeover-gate',
  [switch]$RequireNewProfile,
  [string]$PolicyFile = (Join-Path $PSScriptRoot 'egress-policy.takeover-gate.json')
)
$ErrorActionPreference = 'Stop'
$old = (docker inspect $Name | ConvertFrom-Json)[0]
$envMap = @{}
foreach ($e in $old.Config.Env) { $p = $e -split '=', 2; $envMap[$p[0]] = $p[1] }
$botId = $envMap['COMPUTER_BOT_ID']; $token = $envMap['COMPUTER_TOKEN']
if (-not $botId -or -not $token) { throw 'bot id/token not found on previous container' }

if ($RequireNewProfile) {
  # docker writes "no such volume" to stderr, which -ErrorAction Stop would turn into a failure: list instead.
  $existing = @(docker volume ls --quiet) | Where-Object { $_ -eq $ProfileVolume }
  if ($existing) { throw "profile volume $ProfileVolume already exists; -RequireNewProfile needs a never-used name" }
}

docker stop $Name | Out-Null
docker rename $Name "$Name-prev" | Out-Null
docker update --restart=no "$Name-prev" | Out-Null

$labels = @()
foreach ($l in $old.Config.Labels.PSObject.Properties) {
  if ($l.Name -like 'com.docker.compose.*' -or $l.Name -like 'openbot.*') { $labels += '--label'; $labels += "$($l.Name)=$($l.Value)" }
}
$args2 = @('create', '--name', $Name, '--restart', 'unless-stopped', '--network', 'bridge',
  '--security-opt', 'no-new-privileges:true', '--cap-drop', 'ALL', '--shm-size', '1g', '--pids-limit', '512',
  '-p', "127.0.0.1:${HostPort}:4100",
  '-v', "${ProfileVolume}:/profiles", '-v', 'openbot-workspace-takeover-gate:/workspace',
  '-e', 'COMPUTER_BOT_ID', '-e', 'COMPUTER_TOKEN', '-e', 'PORT=4100', '-e', 'WORKSPACE_DIR=/workspace') + $labels + @($Image)
$env:COMPUTER_BOT_ID = $botId; $env:COMPUTER_TOKEN = $token
docker @args2 | Out-Null
docker start $Name | Out-Null
Remove-Item Env:COMPUTER_BOT_ID, Env:COMPUTER_TOKEN

$healthy = $false
for ($i = 0; $i -lt 60; $i++) {
  $s = docker inspect $Name --format '{{.State.Health.Status}}'
  if ($s -eq 'healthy') { $healthy = $true; break }
  Start-Sleep 2
}
"PROFILE_VOLUME=$ProfileVolume"
"PROFILE_REUSED=" + $(if ($RequireNewProfile) { 'NO' } else { 'UNKNOWN' })
"CONTAINER_RUNNING=" + (docker inspect $Name --format '{{.State.Running}}')
"HEALTH=" + $(if ($healthy) { 'PASS' } else { 'FAIL' })
if (-not $healthy) { exit 1 }

$body = (Get-Content $PolicyFile -Raw).Trim()
$pol = $body | ConvertFrom-Json
$ok = $false
try {
  $r = Invoke-WebRequest -Method Put -Uri "http://127.0.0.1:$HostPort/egress-policy" -ContentType 'application/json' -Body ('{"policy":' + $body + '}') -Headers @{ 'x-openbot-computer-token' = $token } -UseBasicParsing
  $ok = ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
} catch { "PUT_STATUS=" + $_.Exception.Response.StatusCode.value__ }
"EGRESS_POLICY_MODE=$($pol.mode)"
"EGRESS_POLICY_RULE_COUNT=$(@($pol.rules).Count)"
"EGRESS_POLICY_PUSH=" + $(if ($ok) { 'PASS' } else { 'FAIL' })
"HOST_PORT=" + (docker port $Name 4100)
"RESTART_COUNT=" + (docker inspect $Name --format '{{.RestartCount}}')
"NEW_IMAGE_ID=" + (docker inspect $Name --format '{{.Image}}')
