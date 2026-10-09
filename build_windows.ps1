$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:PAYCHECK_SENTINEL_DB = "sqlite"

python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

python -m pip install -r requirements-windows.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }

python -m PyInstaller --noconfirm --clean bailiff_sentinel_windows.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host ""
Write-Host "Build output: dist\BailiffSentinel\BailiffSentinel.exe"
Write-Host "Run the executable and open http://127.0.0.1:5000 in your browser."
