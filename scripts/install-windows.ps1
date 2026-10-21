# Installs or updates OptionsJourney on Windows (decisions 0007, 0094), touching nothing
# outside your user folder:
#   1. uv, the tool that installs Python and the app's packages (skipped if
#      you already have it)
#   2. copies the app to %LOCALAPPDATA%\Programs\OptionsJourney (an update replaces the
#      old copy; your trades and settings live elsewhere and are kept)
#   3. Python and the app's packages into that folder (.venv)
#   4. an OptionsJourney entry in the Start menu, and OptionsJourney.bat in that folder
#   5. starts the app: an OJ icon in the system tray, and your browser opens it
#
# Usage: double-click "Install OptionsJourney.bat", or run
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 [-NoStart]
# $env:OJ_INSTALL_DIR installs somewhere else (for testing).
param([switch]$NoStart)
$ErrorActionPreference = "Stop"

$SrcDir = Split-Path -Parent $PSScriptRoot
$Dest = if ($env:OJ_INSTALL_DIR) { $env:OJ_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\OptionsJourney" }
function Say($text) { Write-Host "`n==> $text" }

Say "Installing OptionsJourney in $Dest"

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

# Any running copy (this one, or one started from a download folder) holds its files
# and the app's port: stop it first.
$Running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match "optionsjourney" }
if ($Running) {
    Say "Stopping the running copy of OptionsJourney"
    $Running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
if ((Resolve-Path $Dest).Path -ne (Resolve-Path $SrcDir).Path) {
    Say "Copying the app (your data is kept: it isn't in this folder)"
    robocopy $SrcDir $Dest /MIR /XD .venv .git /XF OptionsJourney.bat /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the app failed (robocopy code $LASTEXITCODE)." }
}

Say "Installing Python and the app's packages (the first time takes a minute or two)"
Set-Location $Dest
& $Uv sync --frozen --no-dev
if ($LASTEXITCODE -ne 0) { throw "Installing the app's packages failed." }

# pythonw.exe: no console window (closing one would stop the app). Logs go to a file.
$Pythonw = Join-Path $Dest ".venv\Scripts\pythonw.exe"
$Launcher = Join-Path $Dest "OptionsJourney.bat"
@"
@echo off
rem Starts OptionsJourney: an OJ icon in the system tray, and the app in your browser.
cd /d "$Dest"
start "" "$Pythonw" -m optionsjourney.launcher
"@ | Set-Content -Encoding ASCII $Launcher

Say "Adding OptionsJourney to the Start menu"
$Shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\OptionsJourney.lnk"
$Link = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut)
$Link.TargetPath = $Pythonw
$Link.Arguments = "-m optionsjourney.launcher"
$Link.WorkingDirectory = $Dest
$Link.Description = "OptionsJourney"
$Link.Save()

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (-not $env:OJ_INSTALL_DIR -and (Get-ItemProperty -Path $RunKey -Name OptionsJourney -ErrorAction SilentlyContinue)) {
    Say "Pointing Start at login to the new copy"
    Set-ItemProperty -Path $RunKey -Name OptionsJourney -Value "`"$Pythonw`" `"-m`" `"optionsjourney.launcher`""
}

if (-not $NoStart) {
    Say "Starting OptionsJourney"
    Start-Process -FilePath $Pythonw -ArgumentList "-m", "optionsjourney.launcher" -WorkingDirectory $Dest
    Write-Host "Look for the OJ icon in the system tray (under ^). Your browser opens the app in a moment."
}

Say "Done. To start OptionsJourney later, choose OptionsJourney in the Start menu"
Write-Host "(or tick 'Start at login' in the OJ menu). You can delete the downloaded folder now."
