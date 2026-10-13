[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $EnvFile,

    [switch] $ValidateOnly
)

$ErrorActionPreference = 'Stop'
$allowedKeys = @('IAOS_COMPUTER_URL', 'IAOS_COMPUTER_BOT_ID', 'IAOS_COMPUTER_TOKEN')
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot '.venv-win\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) { throw 'Computer runtime env file is not a file' }
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { throw "IAOS Python runtime not found: $pythonExe" }

$values = @{}
$lineNumber = 0
foreach ($line in ([IO.File]::ReadAllLines([IO.Path]::GetFullPath($EnvFile)))) {
    $lineNumber++
    $trimmed = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith('#')) { continue }
    $separator = $trimmed.IndexOf('=')
    if ($separator -le 0) { throw "Invalid computer runtime env line: $lineNumber" }
    $key = $trimmed.Substring(0, $separator).Trim()
    $value = $trimmed.Substring($separator + 1).Trim()
    if ($allowedKeys -notcontains $key) { throw "Unknown computer runtime env key: $key" }
    if ($values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($value)) { throw "Invalid computer runtime env value: $key" }
    $values[$key] = $value
}
foreach ($key in $allowedKeys) { if (-not $values.ContainsKey($key)) { throw "Missing computer runtime env key: $key" } }

if ($ValidateOnly) {
    Write-Output 'IAOS_COMPUTER_RUNTIME_CONFIG_VALID=YES'
    Write-Output 'SECRET_VALUES_PRINTED=NO'
    return
}

$childEnvironment = @{
    IAOS_COMPUTER_URL = $values['IAOS_COMPUTER_URL']
    IAOS_COMPUTER_BOT_ID = $values['IAOS_COMPUTER_BOT_ID']
    IAOS_COMPUTER_TOKEN = $values['IAOS_COMPUTER_TOKEN']
}
try {
    Start-Process -FilePath $pythonExe -ArgumentList @('-m', 'application.computer_runtime_server') `
        -WorkingDirectory $repoRoot -Environment $childEnvironment | Out-Null
    Write-Output 'IAOS_COMPUTER_RUNTIME_STARTED=YES'
    Write-Output 'COMPUTER_TOKEN_PRINTED=NO'
}
finally {
    $childEnvironment = $null
    $values = $null
}
