# Getting started (developers)

## Requirements
- macOS or Windows
- [uv](https://docs.astral.sh/uv/), which installs the right Python version for you.
  - Mac: `brew install uv`
  - Windows: `winget install --id=astral-sh.uv`

## Run it

```bash
git clone https://github.com/optionsjourney/optionsjourney.git
cd optionsjourney
cp .env.example .env      # edit if needed
uv run optionsjourney
```

Then open <http://127.0.0.1:2026>. Or run `uv run optionsjourney-menubar` for the menu bar app (decision 0008): it runs the same app in the background with a status icon, and opens the browser. The first run installs Python 3.14 and the dependencies into `.venv/`. Both log to the console and to `logs/optionsjourney.log` in the data folder (`optionsjourney/logs.py`), and both start the same background jobs (`services/background.start_all`). Users install with `scripts/install-mac.sh` or `install-windows.ps1`, which copy the app to a fixed folder (decision 0094); `OJ_INSTALL_DIR=<folder>` installs somewhere else for testing.

## Test and lint

```bash
uv run pytest             # tests
uv run ruff check .       # lint
uv run ruff format .      # format
```

Run all three before committing.

## Settings
There are two kinds:
- **The Settings page** (`/settings`): everything a user sets. Each setting is declared once in `core/settings.py`, in a section, with a label, a one-line `help` (shown under the label), a fuller `tip` (in the ⓘ tooltip), a default and a check. The page shows it with no further work: a sidebar of sections, one shown at a time (`js/settings_sections.js`), each setting a row. Tooltips anywhere use the macros in `templates/_tooltip.html`. Values are saved in the `settings` table, and a setting with no row uses its default (`services/settings.get`). **Add new settings here** ([0021](../decisions/0021-product-scope.md)).
- **`.env`**: only what's needed before the database can open, plus gateway mode for development. These are read in one place, `src/optionsjourney/config.py`, and `.env.example` lists them.

| Setting | Default | Meaning |
| --- | --- | --- |
| `BROKER_CREDENTIALS` | `local` | Where broker tokens come from (see [broker-credentials.md](broker-credentials.md)) |
| `GATEWAY_URL`, `GATEWAY_SECRET` | none | Gateway mode only |
| `PORT` | `2026` | Port for the web server |
| `LISTEN_ON_NETWORK` | `no` | `yes` lets other devices on the network open the app; there is no login (see [0063](../decisions/0063-optional-local-network-access.md)). The **Open from other devices** switch in Settings does the same and survives updates; either one turns it on, except in demo mode, which ignores the switch ([0104](../decisions/0104-open-from-other-devices-is-a-settings-switch.md)) |
| `OPTIONSJOURNEY_DATA_DIR` | your user data folder | Where the database lives. On Mac: `~/Library/Application Support/OptionsJourney` |

## Health check
`GET /health` reports the version, the credentials mode, whether the trading guard is on, and whether the database works.
