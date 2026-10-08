# Builds the MERLIN Windows bundle:
#   1) python venv + dependencies
#   2) PyInstaller one-folder bundle -> dist\Merlin\
#   3) Inno Setup installer (if ISCC.exe is available) -> dist\MERLIN-Setup.exe
# Run from the project root:  powershell -ExecutionPolicy Bypass -File installer\build_windows.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot   # installer\.. -> project root
Set-Location $root

Write-Host "== 1/3 Creating venv and installing dependencies ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".venv\Scripts\pip.exe" install -r requirements.txt
& ".venv\Scripts\pip.exe" install pyinstaller

Write-Host "== 2/3 PyInstaller bundle ==" -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -m PyInstaller merlin.spec --noconfirm

Write-Host "== 3/3 Inno Setup installer ==" -ForegroundColor Cyan
$iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if ($iscc) {
    & $iscc.Source "installer\windows\merlin_win.iss"
    Write-Host "Installer written to dist\MERLIN-Setup.exe" -ForegroundColor Green
} else {
    Write-Warning "Inno Setup (ISCC.exe) not found on PATH. Install Inno Setup to produce the installer,"
    Write-Warning "or distribute the portable folder dist\Merlin\ directly."
}
