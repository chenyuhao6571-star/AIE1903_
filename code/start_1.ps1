param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run setup.ps1 first.'
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'frontend\dist\index.html'))) {
    throw 'Frontend is not built. Run setup.ps1 first.'
}
Write-Host "CV Reader: http://127.0.0.1:$Port"
& $pythonPath -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
