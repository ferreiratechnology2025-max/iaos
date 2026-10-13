[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $EnvFile,

    [switch] $ValidateOnly
)

$ErrorActionPreference = 'Stop'
$allowedKeys = @(
    'BRAVE_SEARCH_API_KEY',
    'AGNES_API_KEY',
    'IAOS_GOOGLE_CLIENT_SECRET_FILE'
)
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot '.venv-win\Scripts\python.exe'

function Read-ExplicitEnvFile([string] $source) {
    $resolved = [IO.Path]::GetFullPath($source)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Secret bundle is not a file: $resolved"
    }

    $values = @{}
    $lineNumber = 0
    foreach ($line in ([IO.File]::ReadAllLines($resolved))) {
        $lineNumber++
        $trimmedLine = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmedLine) -or $trimmedLine.StartsWith('#')) {
            continue
        }
        $separator = $trimmedLine.IndexOf('=')
        if ($separator -le 0) {
            throw "Invalid secret bundle line: $lineNumber"
        }
        $key = $trimmedLine.Substring(0, $separator).Trim()
        $value = $trimmedLine.Substring($separator + 1).Trim()
        if ($allowedKeys -notcontains $key) {
            throw "Unknown secret bundle key: $key"
        }
        if ($values.ContainsKey($key)) {
            throw "Duplicate secret bundle key: $key"
        }
        if ([string]::IsNullOrWhiteSpace($value)) {
            throw "Empty secret bundle value: $key"
        }
        $values[$key] = $value
    }

    foreach ($requiredKey in $allowedKeys) {
        if (-not $values.ContainsKey($requiredKey)) {
            throw "Missing secret bundle key: $requiredKey"
        }
    }
    return $values
}

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "IAOS Python runtime not found: $pythonExe"
}
$bundle = Read-ExplicitEnvFile $EnvFile
$braveSecret = $bundle['BRAVE_SEARCH_API_KEY']
$agnesSecret = $bundle['AGNES_API_KEY']
$googleSecretFile = $bundle['IAOS_GOOGLE_CLIENT_SECRET_FILE']
if (-not (Test-Path -LiteralPath $googleSecretFile -PathType Leaf)) {
    throw "Google client secret file not found"
}

if ($ValidateOnly) {
    Write-Output 'IAOS_SECRET_BUNDLE_VALID=YES'
    Write-Output 'SECRET_VALUES_PRINTED=NO'
    Write-Output 'BACKEND_STARTED=NO'
    return
}

$childEnvironment = @{
    BRAVE_SEARCH_API_KEY          = $braveSecret
    AGNES_API_KEY                 = $agnesSecret
    IAOS_GOOGLE_CLIENT_SECRET_FILE = $googleSecretFile
    IAOS_GOOGLE_UI_ORIGIN      = 'http://127.0.0.1:5173'
}

try {
    $child = Start-Process -FilePath $pythonExe `
        -ArgumentList @('-m', 'application.google_integration_service') `
        -WorkingDirectory $repoRoot `
        -Environment $childEnvironment `
        -PassThru

    # Do not print values. These are presence checks for the values loaded above.
    Write-Output 'IAOS_BACKEND_STARTED_BY_LAUNCHER=YES'
    Write-Output 'BRAVE_SEARCH_API_KEY=PRESENT'
    Write-Output 'AGNES_API_KEY=PRESENT'
    Write-Output 'IAOS_GOOGLE_CLIENT_SECRET_FILE=PRESENT'

    $healthUrl = 'http://127.0.0.1:8766/integrations/google'
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $status = $null
    do {
        Start-Sleep -Milliseconds 250
        try { $status = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2 } catch { $status = $null }
    } while ($null -eq $status -and [DateTime]::UtcNow -lt $deadline -and -not $child.HasExited)

    if ($null -eq $status) {
        throw 'IAOS backend did not become healthy'
    }

    Write-Output ('GOOGLE_CONNECTION_CONNECTED=' + ($(if ($status.connected) { 'YES' } else { 'NO' })))
    Write-Output ('GOOGLE_CALENDAR_CONNECTED=' + ($(if ($status.calendar) { 'YES' } else { 'NO' })))
    Write-Output ('GOOGLE_GMAIL_CONNECTED=' + ($(if ($status.gmail) { 'YES' } else { 'NO' })))
    Write-Output 'BRAVE_SECRET_VALUE_PRINTED=NO'
    Write-Output 'AGNES_SECRET_VALUE_PRINTED=NO'

    if (-not ($status.connected -and $status.calendar -and $status.gmail)) {
        throw 'GoogleConnection precheck failed'
    }
}
finally {
    # Remove local references; Start-Process received a separate environment snapshot.
    $braveSecret = $null
    $agnesSecret = $null
    $childEnvironment = $null
}
