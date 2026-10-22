# Installs or updates OptionsJourney on Windows (decisions 0007, 0094), touching nothing
# outside your user folder:
#   1. uv, the tool that installs Python and the app's packages (skipped if
#      you already have it)
#   2. copies the app to %LOCALAPPDATA%\Programs\OptionsJourney (an update replaces the
#      old copy, which is put back if the update fails; your trades and settings live
#      elsewhere and are kept)
#   3. Python and the app's packages into that folder (.venv)
#   4. an OptionsJourney entry in the Start menu, and OptionsJourney.bat in that folder
#   5. starts the app: an OJ icon in the system tray, and your browser opens it
#
# Usage: double-click "Install OptionsJourney for Windows.bat", or run
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 [-NoStart]
# $env:OJ_INSTALL_DIR installs somewhere else (for testing).
param([switch]$NoStart)
$ErrorActionPreference = "Stop"

$SrcDir = Split-Path -Parent $PSScriptRoot
$Dest = if ($env:OJ_INSTALL_DIR) { $env:OJ_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\OptionsJourney" }
function Say($text) { Write-Host "`n==> $text" }
# Only uv's own Python, never one already on the computer (same as the Mac installer).
$env:UV_PYTHON_PREFERENCE = "only-managed"

Say "Installing OptionsJourney in $Dest"

$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv) {
    $Candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $Candidate) { $Uv = $Candidate }
}
if (-not $Uv) {
    Say "Getting uv (into your user folder, nothing else is changed)"
    $env:UV_NO_MODIFY_PATH = "1"
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Host
    $Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
}
Write-Host "Using uv at $Uv"

function Sync-Packages {
    # uv's output goes through PowerShell (Out-Host) so the update log, a transcript,
    # keeps it. uv reports progress on stderr, which Windows PowerShell 5.1 would treat
    # as an error under "Stop": this function alone uses "Continue".
    $ErrorActionPreference = "Continue"
    & $Uv sync --frozen --no-dev 2>&1 | ForEach-Object { "$_" } | Out-Host
    return $LASTEXITCODE
}

# pythonw.exe: no console window (closing one would stop the app). Logs go to a file.
$Pythonw = Join-Path $Dest ".venv\Scripts\pythonw.exe"
$Launcher = Join-Path $Dest "OptionsJourney.bat"
function Write-Launcher {
    # Written under a temporary name and renamed, so it only exists complete: it
    # marks a complete copy (see the leftover check below).
    @"
@echo off
rem Starts OptionsJourney: an OJ icon in the system tray, and the app in your browser.
cd /d "$Dest"
start "" "$Pythonw" -m optionsjourney.launcher
"@ | Set-Content -Encoding ASCII "$Launcher.partial"
    Move-Item -Force -Path "$Launcher.partial" -Destination $Launcher
}

function Start-OldCopy {
    $OldPythonw = Join-Path $Dest ".venv\Scripts\pythonw.exe"
    if (-not $NoStart -and (Test-Path $OldPythonw) -and (Test-Path $Launcher)) {
        Start-Process -FilePath $OldPythonw -ArgumentList "-m", "optionsjourney.launcher" -WorkingDirectory $Dest
    }
}

# Any running copy (this one, or one started from a download folder) holds its files
# and the app's port: stop it first.
$Running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match "optionsjourney" }
if ($Running) {
    Say "Stopping the running copy of OptionsJourney"
    $Running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

# The old copy is set aside as <folder>.previous and put back if anything below fails.
$Previous = "$Dest.previous"
# Running the installer from inside the install folder: it can't be moved aside.
$InPlace = (Test-Path $Dest) -and ((Resolve-Path $Dest).Path -eq (Resolve-Path $SrcDir).Path)

if (-not $InPlace) {
    # Windows can't rename a folder that is any process's working directory, this
    # one's included.
    Set-Location $env:USERPROFILE
    [Environment]::CurrentDirectory = $env:USERPROFILE
    try {
        if ((Test-Path $Dest) -and (Test-Path $Previous)) {
            # Left over from an earlier run that stopped half way. The launcher marks a
            # complete copy and $Previous is deleted only after it is written, so a $Dest
            # without it is a partial copy and $Previous the real old version.
            if (Test-Path $Launcher) {
                Remove-Item -Recurse -Force $Previous
            } else {
                Remove-Item -Recurse -Force $Dest
            }
        }
        if (Test-Path $Dest) {
            Rename-Item -Path $Dest -NewName (Split-Path -Leaf $Previous)
        }   # (no $Dest but a $Previous: that one is the old version to put back)
    } catch {
        Write-Host ""
        Write-Host "Error: $($_.Exception.Message)"
        Write-Host "The update couldn't start: $Dest couldn't be moved aside (a file in it may be in use)."
        Write-Host "Nothing was changed in your data."
        Write-Host "Close any window showing that folder, then run the update again."
        Start-OldCopy
        exit 1
    }
    try {
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        Say "Copying the app (your data is kept: it isn't in this folder)"
        robocopy $SrcDir $Dest /MIR /XD .venv .git /XF OptionsJourney.bat /NFL /NDL /NJH /NJS /NP | Out-Host
        if ($LASTEXITCODE -ge 8) { throw "Copying the app failed (robocopy code $LASTEXITCODE)." }

        Say "Installing Python and the app's packages (the first time takes a minute or two)"
        Set-Location $Dest
        if ((Sync-Packages) -ne 0) { throw "Installing the app's packages failed." }
        Write-Launcher
    } catch {
        Write-Host ""
        Write-Host "Error: $($_.Exception.Message)"
        Set-Location $env:USERPROFILE
        if (Test-Path $Previous) {
            if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest -ErrorAction SilentlyContinue }
            if (Test-Path $Dest) {
                Write-Host "The update didn't finish. Your previous version is in $Previous; run the installer again."
                exit 1
            }
            Rename-Item -Path $Previous -NewName (Split-Path -Leaf $Dest)
            Write-Host "The update didn't finish, so the previous version was put back."
            Write-Host "Nothing was changed in your data."
            Start-OldCopy
        } else {
            Write-Host "The install didn't finish. Check your internet connection and try again."
        }
        exit 1
    }
    # The new copy is complete: the old one is no longer needed. Last, and best effort
    # (a file held open can't be deleted): a leftover is cleared up on the next run.
    if (Test-Path $Previous) { Remove-Item -Recurse -Force $Previous -ErrorAction SilentlyContinue }
} else {
    Say "Installing Python and the app's packages (the first time takes a minute or two)"
    Set-Location $Dest
    if ((Sync-Packages) -ne 0) { throw "Installing the app's packages failed." }
    Write-Launcher
}

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
