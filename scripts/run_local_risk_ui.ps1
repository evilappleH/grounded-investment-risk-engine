param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot

if (-not $PythonPath) {
    $resolved = Get-Command python -ErrorAction SilentlyContinue
    if (-not $resolved) {
        throw 'Python was not found on PATH. Pass -PythonPath with the interpreter to use.'
    }
    $PythonPath = $resolved.Source
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python was not found: $PythonPath"
}

Set-Location -LiteralPath $repository
Write-Host "Starting Sentinel local risk console on http://127.0.0.1:$Port"
Write-Host 'Real-account reads are enabled; broker write capability is absent.'
& $PythonPath -m investment_agents.user_app.server --host 127.0.0.1 --port $Port
