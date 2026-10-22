$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python 3.11 environment' }
}
& .venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .venv/Scripts/python.exe -m PyInstaller desktop.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
Write-Host 'Build ready: dist/BiliBiliLuckyDraw/BiliBiliLuckyDraw.exe'
