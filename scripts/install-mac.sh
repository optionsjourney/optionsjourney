#!/bin/bash
# Installs or updates OptionsJourney on a Mac (decisions 0007, 0094), touching nothing
# outside your home folder:
#   1. uv, the tool that installs Python and the app's packages, into
#      ~/.local/bin (skipped if you already have it)
#   2. copies the app to ~/Applications/OptionsJourney (an update replaces the old
#      copy; your trades and settings live elsewhere and are kept)
#   3. Python and the app's packages into that folder (.venv)
#   4. OptionsJourney.app next to it (Spotlight and Launchpad find it), and
#      OptionsJourney.command inside it: either starts the app
#   5. starts the app: an OJ icon in the menu bar, and your browser opens it
#
# Usage: double-click "Install OptionsJourney.command", or run
#   bash scripts/install-mac.sh [--no-start]
# OJ_INSTALL_DIR=<folder> installs somewhere else (for testing).
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${OJ_INSTALL_DIR:-$HOME/Applications/OptionsJourney}"
START=1
[[ "${1:-}" == "--no-start" ]] && START=0

say() { printf '\n==> %s\n' "$1"; }

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

mkdir -p "$DEST"
if [[ "$(cd "$DEST" && pwd)" != "$SRC_DIR" ]]; then
    say "Copying the app (your data is kept: it isn't in this folder)"
    rsync -a --delete --exclude .venv --exclude .git --exclude OptionsJourney.command \
        "$SRC_DIR/" "$DEST/"
fi

say "Installing Python and the app's packages (the first time takes a minute or two)"
cd "$DEST"
"$UV" sync --frozen --no-dev

PYTHON="$DEST/.venv/bin/python"
LAUNCHER="$DEST/OptionsJourney.command"
cat > "$LAUNCHER" <<LAUNCH
#!/bin/bash
# Starts OptionsJourney: an OJ icon in the menu bar, and the app in your browser.
cd "$DEST"
nohup "$PYTHON" -m optionsjourney.launcher >/dev/null 2>&1 &
LAUNCH
chmod +x "$LAUNCHER"

APP="$(dirname "$DEST")/OptionsJourney.app"
say "Making $APP"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"/bin/bash \" & quoted form of \"$LAUNCHER\""

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
