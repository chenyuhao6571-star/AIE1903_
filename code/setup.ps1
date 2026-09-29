param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Python) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $Python = $bundledPython }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = 'python' }
    else { throw 'Python 3.11+ is required. Pass its path with -Python.' }
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
Push-Location frontend
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Could not install frontend dependencies.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Could not build frontend.' }
} finally { Pop-Location }
Write-Host 'Ready. Run .\start.ps1'
