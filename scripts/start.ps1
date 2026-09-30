param([switch]$Setup, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$projectPython = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw '请先安装 Python 3.11+，并启用 PATH。' }
    & $pythonCommand.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败。' }
    $Setup = $true
}
if ($Setup) {
    & $projectPython -m pip install -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw '安装依赖失败。' }
    & $projectPython -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw '安装项目失败。' }
    & $projectPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw '安装 Chromium 失败。' }
}
$studioPort = & $projectPython -c 'from autoqa.config import Settings; print(Settings().port)'
if ($LASTEXITCODE -ne 0) { throw '读取配置失败。' }
Write-Host "AutoQA Studio: http://127.0.0.1:$studioPort" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$studioPort" }
& $projectPython -m uvicorn autoqa.app:app --host 127.0.0.1 --port $studioPort --workers 1 --no-access-log
