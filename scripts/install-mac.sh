#!/bin/bash
# Installs or updates OptionsJourney on a Mac (decisions 0007, 0094), touching nothing
# outside your home folder:
#   1. uv, the tool that installs Python and the app's packages, into
#      ~/.local/bin (skipped if you already have it)
#   2. copies the app to ~/Applications/OptionsJourney (an update replaces the old
#      copy, which is put back if the update fails; your trades and settings live
#      elsewhere and are kept)
#   3. Python and the app's packages into that folder (.venv)
#   4. OptionsJourney.app next to it (Spotlight and Launchpad find it), and
#      OptionsJourney.command inside it: either starts the app
#   5. starts the app: an OJ icon in the menu bar, and your browser opens it
#
# Usage: double-click "Install OptionsJourney for Mac.command", or run
#   bash scripts/install-mac.sh [--no-start]
# OJ_INSTALL_DIR=<folder> installs somewhere else (for testing).
set -Eeuo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${OJ_INSTALL_DIR:-$HOME/Applications/OptionsJourney}"
START=1
[[ "${1:-}" == "--no-start" ]] && START=0

say() { printf '\n==> %s\n' "$1"; }
# Only uv's own Python: looking at /usr/bin/python3 on a fresh Mac pops up
# "install the command line developer tools", which the app doesn't need.
export UV_PYTHON_PREFERENCE=only-managed
# Setting up its Python, uv runs install_name_tool. Without the developer tools,
# /usr/bin/install_name_tool only pops up the same offer. A stand-in that fails
# quietly comes first instead: uv notes it and carries on (nothing here needs it).
if ! xcode-select -p >/dev/null 2>&1; then
    NO_DEV_TOOLS="$(mktemp -d)"
    printf '#!/bin/sh\nexit 1\n' > "$NO_DEV_TOOLS/install_name_tool"
    chmod +x "$NO_DEV_TOOLS/install_name_tool"
    export PATH="$NO_DEV_TOOLS:$PATH"
fi

say "Installing OptionsJourney in $DEST"

UV="$(command -v uv || true)"
if [[ -z "$UV" && -x "$HOME/.local/bin/uv" ]]; then
    UV="$HOME/.local/bin/uv"
fi
if [[ -z "$UV" ]]; then
    say "Getting uv (into ~/.local/bin, nothing else is changed)"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
    UV="$HOME/.local/bin/uv"
fi
echo "Using uv at $UV"

if pkill -f "$DEST/.venv/bin/" 2>/dev/null; then
    say "Stopped the running copy of OptionsJourney"
    sleep 2
fi

# The old copy is set aside as $DEST.previous and put back if anything below fails.
PREVIOUS="$DEST.previous"
restore_previous() {
    local status=$?
    # Messages to a closed Terminal fail quietly (SIGPIPE is ignored), and a second
    # signal can't stop the restore half way.
    trap '' PIPE HUP INT TERM
    trap - ERR
    set +e
    cd "$HOME"
    echo
    if [[ -d "$PREVIOUS" ]]; then
        rm -rf "$DEST"
        if [[ ! -e "$DEST" ]]; then
            mv "$PREVIOUS" "$DEST"
            echo "The update didn't finish, so the previous version was put back."
            echo "Nothing was changed in your data."
            if [[ "$START" == 1 && -f "$DEST/OptionsJourney.command" ]]; then
                # Normal signals again for the restarted app, or the next update can't stop it
                ( trap - PIPE HUP INT TERM; bash "$DEST/OptionsJourney.command" )
            fi
        else
            echo "The update didn't finish. Your previous version is in $PREVIOUS; run the installer again."
        fi
    else
        echo "The install didn't finish. Check your internet connection and try again."
    fi
    exit "$((status == 0 ? 1 : status))"
}

# Running the installer from inside the install folder: it can't be moved aside.
IN_PLACE=0
if [[ -d "$DEST" && "$(cd "$DEST" && pwd)" == "$SRC_DIR" ]]; then
    IN_PLACE=1
fi

if [[ "$IN_PLACE" == 0 ]]; then
    if [[ -d "$DEST" && -d "$PREVIOUS" ]]; then
        # Left over from an earlier run that stopped half way. The launcher marks a
        # complete copy and $PREVIOUS is deleted only after it is written, so a $DEST
        # without it is a partial copy and $PREVIOUS the real old version.
        if [[ -f "$DEST/OptionsJourney.command" ]]; then
            rm -rf "$PREVIOUS"
        else
            rm -rf "$DEST"
        fi
    fi
    if [[ -d "$DEST" ]]; then
        mv "$DEST" "$PREVIOUS"
    fi   # (no $DEST but a $PREVIOUS: that one is the old version to put back)
    # Closing the Terminal window also stops the tee writing the log; bash then reports
    # the stopped uv on that closed pipe. Ignoring SIGPIPE lets such writes fail quietly
    # so the restore below still runs.
    trap '' PIPE
    trap restore_previous ERR INT TERM HUP
    mkdir -p "$DEST"
    say "Copying the app (your data is kept: it isn't in this folder)"
    rsync -a --exclude .venv --exclude .git --exclude OptionsJourney.command \
        "$SRC_DIR/" "$DEST/"
else
    mkdir -p "$DEST"
fi

say "Installing Python and the app's packages (the first time takes a minute or two)"
cd "$DEST"
"$UV" sync --frozen --no-dev

# The launcher marks the new copy complete (see the leftover check above).
PYTHON="$DEST/.venv/bin/python"
LAUNCHER="$DEST/OptionsJourney.command"
cat > "$LAUNCHER.partial" <<LAUNCH
#!/bin/bash
# Starts OptionsJourney: an OJ icon in the menu bar, and the app in your browser.
cd "$DEST"
nohup "$PYTHON" -m optionsjourney.launcher >/dev/null 2>&1 &
LAUNCH
chmod +x "$LAUNCHER.partial"
mv "$LAUNCHER.partial" "$LAUNCHER"

# The new copy is complete: the old one is no longer needed. Last, and best effort:
# a half-deleted $PREVIOUS next to a complete copy is cleared up on the next run.
trap - ERR INT TERM HUP
rm -rf "$PREVIOUS" 2>/dev/null || true

APP="$(dirname "$DEST")/OptionsJourney.app"
say "Making $APP"
rm -rf "$APP"
# osacompile's "replacing existing signature" note reads like an error: show anything else
if ! OSA_OUT="$(osacompile -o "$APP" -e "do shell script \"/bin/bash \" & quoted form of \"$LAUNCHER\"" 2>&1)"; then
    printf '%s\n' "$OSA_OUT"
    exit 1
fi
printf '%s\n' "$OSA_OUT" | grep -v "replacing existing signature" | grep -v '^$' || true

AGENT="$HOME/Library/LaunchAgents/com.optionsjourney.app.plist"
if [[ -f "$AGENT" && -z "${OJ_INSTALL_DIR:-}" ]]; then
    say "Pointing Start at login to the new copy"
    /usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 $PYTHON" \
        -c "Set :WorkingDirectory $DEST" "$AGENT"
fi

if [[ "$START" == 1 ]]; then
    say "Starting OptionsJourney"
    bash "$LAUNCHER"
    echo "Look for the OJ icon in the menu bar. Your browser opens the app in a moment."
fi

say "Done. To start OptionsJourney later, open OptionsJourney from Spotlight or Launchpad"
echo "(or tick 'Start at login' in the OJ menu). You can delete the downloaded folder now."
