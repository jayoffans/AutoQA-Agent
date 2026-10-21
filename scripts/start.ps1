param([switch]$Setup, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$projectPython = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        & $pythonLauncher.Source -3 -m venv .venv
    } elseif ($pythonCommand) {
        & $pythonCommand.Source -m venv .venv
    } else { throw 'Install Python 3.11+ and enable its PATH entry first.' }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
    $Setup = $true
}
if ($Setup) {
    & $projectPython -m pip install -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the locked dependencies.' }
    & $projectPython -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the project.' }
    & $projectPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Chromium.' }
}
$configurationCheck = @'
import sys
try:
    from autoqa.config import Settings
    if sys.version_info < (3, 11):
        raise RuntimeError
    print(Settings().port)
except Exception:
    sys.exit('Cannot read startup settings; use Python 3.11+ and check your local configuration.')
'@
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
$studioPort = & $projectPython -c $configurationCheck
if ($LASTEXITCODE -ne 0) { throw 'Could not read startup settings; check your local configuration.' }
Write-Host "AutoQA Studio: http://127.0.0.1:$studioPort" -ForegroundColor Green
$browserJob = $null
if (-not $NoBrowser) {
    $browserJob = Start-Job -ArgumentList $studioPort -ScriptBlock {
        param($port)
        $studioUrl = "http://127.0.0.1:$port"
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            try {
                $response = Invoke-WebRequest -Uri "$studioUrl/health" -UseBasicParsing -TimeoutSec 1
                if ($response.StatusCode -eq 200) { Start-Process $studioUrl; return }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
    }
}
$env:HOST = '127.0.0.1'
$serviceExitCode = 1
try {
    & $projectPython -m autoqa
    $serviceExitCode = $LASTEXITCODE
} finally {
    if ($browserJob) {
        Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
    }
}
exit $serviceExitCode
