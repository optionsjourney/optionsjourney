# Installs OptionsJourney on Windows (docs/decisions/0007), touching nothing
# outside your user folder:
#   1. uv, the tool that installs Python and the app's packages (skipped if
#      you already have it)
#   2. Python and the app's packages into this folder (.venv)
#   3. "OptionsJourney.bat" in this folder: double-click it to start the app
#   4. starts the app: an OJ icon in the system tray, and your browser opens it
#
# Usage: double-click "Install OptionsJourney.bat", or run
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 [-NoStart]
param([switch]$NoStart)
$ErrorActionPreference = "Stop"

$AppDir = Split-Path -Parent $PSScriptRoot
function Say($text) { Write-Host "`n==> $text" }

Say "Installing OptionsJourney in $AppDir"

$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv) {
    $Candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $Candidate) { $Uv = $Candidate }
}
if (-not $Uv) {
    Say "Getting uv (into your user folder, nothing else is changed)"
    $env:UV_NO_MODIFY_PATH = "1"
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
}
Write-Host "Using uv at $Uv"

Say "Installing Python and the app's packages (the first time takes a minute or two)"
Set-Location $AppDir
& $Uv sync --frozen --no-dev

Say "Making OptionsJourney.bat (double-click it to start the app)"
$Launcher = Join-Path $AppDir "OptionsJourney.bat"
@"
@echo off
rem Starts OptionsJourney: an OJ icon in the system tray, and the app in your browser.
cd /d "$AppDir"
start "" /min "$Uv" run --frozen --no-dev optionsjourney-menubar
"@ | Set-Content -Encoding ASCII $Launcher

if (-not $NoStart) {
    Say "Starting OptionsJourney"
    Start-Process -FilePath $Launcher -WindowStyle Hidden
    Write-Host "Look for the OJ icon in the system tray. Your browser opens the app in a moment."
}

Say "Done. To start OptionsJourney later, double-click OptionsJourney.bat in $AppDir"
