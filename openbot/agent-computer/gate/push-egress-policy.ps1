# Re-applies the takeover-gate egress policy to the running container (token read in memory, never printed).
param([string]$Name = 'openbot-computer-takeover-gate', [int]$HostPort = 55248,
  [string]$PolicyFile = (Join-Path $PSScriptRoot 'egress-policy.takeover-gate.json'))
$ErrorActionPreference = 'Stop'
$c = (docker inspect $Name | ConvertFrom-Json)[0]
$token = ($c.Config.Env | ? { $_ -like 'COMPUTER_TOKEN=*' }) -replace '^COMPUTER_TOKEN=', ''
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
