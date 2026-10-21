#!/bin/bash
# Installs OptionsJourney on a Mac (docs/decisions/0007), touching nothing
# outside your home folder:
#   1. uv, the tool that installs Python and the app's packages, into
#      ~/.local/bin (skipped if you already have it)
#   2. Python and the app's packages into this folder (.venv)
#   3. "OptionsJourney.command" in this folder: double-click it to start the app
#   4. starts the app: an OJ icon in the menu bar, and your browser opens it
#
# Usage: double-click "Install OptionsJourney.command", or run
#   bash scripts/install-mac.sh [--no-start]
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START=1
[[ "${1:-}" == "--no-start" ]] && START=0

say() { printf '\n==> %s\n' "$1"; }

say "Installing OptionsJourney in $APP_DIR"

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

say "Installing Python and the app's packages (the first time takes a minute or two)"
cd "$APP_DIR"
"$UV" sync --frozen --no-dev

say "Making OptionsJourney.command (double-click it to start the app)"
LAUNCHER="$APP_DIR/OptionsJourney.command"
cat > "$LAUNCHER" <<LAUNCH
#!/bin/bash
# Starts OptionsJourney: an OJ icon in the menu bar, and the app in your browser.
cd "$APP_DIR"
nohup "$UV" run --frozen --no-dev optionsjourney-menubar >/dev/null 2>&1 &
LAUNCH
chmod +x "$LAUNCHER"

if [[ "$START" == 1 ]]; then
    say "Starting OptionsJourney"
    bash "$LAUNCHER"
    echo "Look for the OJ icon in the menu bar. Your browser opens the app in a moment."
fi

say "Done. To start OptionsJourney later, double-click OptionsJourney.command in $APP_DIR"
echo "(Or tick 'Start at login' in the OJ menu.)"
