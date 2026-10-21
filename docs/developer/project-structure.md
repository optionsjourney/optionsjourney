# Project structure

How the repository is organized, and the rules that keep it that way. If you add a folder, add it here.

```
optionsjourney/
├── README.md                 Front page: what the app is, where to read more
├── CHANGELOG.md              What changed in each release
├── LICENSE                 PolyForm Noncommercial 1.0.0 (decision 0093)
├── DISCLAIMER.md           Not financial advice; you are responsible for your orders
├── CONTRIBUTING.md         How ideas and pull requests are handled (applied by hand, decision 0093)
├── CLAUDE.md                 Working notes for the AI assistant that helps build this
├── pyproject.toml            Dependencies and tool settings (one file for all of it)
├── .env.example              Every setting the app reads, with safe placeholder values
├── .gitignore
├── .python-version           Python version for uv (3.14)
├── uv.lock                   Exact dependency versions
│
├── src/optionsjourney/       The application (an installable Python package)
│   ├── __main__.py           Entry point: `uv run optionsjourney`
│   ├── app.py                Creates the Flask app
│   ├── config.py             Reads settings from .env, in one place
│   ├── core/                 Trading logic. Pure Python: no Flask, no database sessions, no broker calls.
│   │                         transactions (fills), orders (grouping), structures (names),
│   │                         positions (matching, rolls, P/L), stats, symbols, market (New York time),
│   │                         sessions (the holiday calendar), economic_calendar (reading public calendars)
│   ├── brokers/              TastyTrade and Schwab clients (tastytrade.py, schwab.py)
│   │   ├── credentials/      Where tokens come from: local logins or the dev gateway
│   │   ├── http.py           The one place broker requests are made and errors translated
│   │   └── guard.py          Blocks trading in gateway mode
│   ├── trading/              Orders, tick sizes, the central check, each broker's order format. Pure.
│   ├── bots/                 Bot engine rules: definitions, validation, strike picking, exit math, the schedule,
│   │                         the floating window, caps, market filters, date lists, the bot file format, integrity checks,
│   │                         stops the app watches, which exit rests at the broker, matching a trade you opened to a bot, resolving each leg's
│   │                         expiration, a futures option's series, a manual bot's exit rules (pure)
│   ├── db/                   Database (plain SQLAlchemy): engine, models.py, migrations/ (Alembic)
│   ├── services/             Ties the pieces together: syncing, scheduling, live P/L
│   ├── web/                  Pages and API
│   │   ├── security.py       Refuses POSTs that don't come from the app's own pages
│   │   ├── formatting.py     Template filters (money, numbers, Eastern time)
│   │   ├── guide.py          Reads docs/guide/*.md (or the wheel's bundled guide/ folder) and renders it to HTML with GitHub-style anchors
│   │   ├── routes/           One file per area (journal, dashboard, setup, settings, …); help_pages.py serves the guide at /help
│   │   ├── sockets.py        Socket.IO events for the charts and the risk graph
│   │   ├── templates/
│   │   └── static/
│   │       ├── css/, js/     This app's own styles and scripts
│   │       ├── images/       The logo, favicon and app icons (from Tracker2)
│   │       ├── js/chart_grid/, js/charts/, css/chart_grid.css
│   │       │                 Tracker2's chart grid, copied as it is (developer/charts.md)
│   │       ├── js/modeler/   The risk graph's pricing and chart drawing, copied from Tracker2
│   │       ├── js/trade/     The Trade page: Tracker2's chain helpers and ticket logic (copied), the inline chain panel, and the page's own code
│   │       └── vendor/       Their libraries: Lightweight Charts, Socket.IO client, Tailwind;
│   │                         vendor/fonts/ holds the charts' IBM Plex fonts
│   └── launcher/             Menu bar / tray app: runs the web app in the background, status icon, start at login, keep awake, show app folder, open logs, restart
│
├── tests/                    Mirrors src/optionsjourney/ (tests/core/, tests/brokers/, …)
│   ├── web/routes/           Page tests by area (manual bots: test_automation_manual.py)
│   └── web/static/js/        Browser-module tests: node --experimental-default-type=module --test tests/web/static/js/ (Node 20+, no npm)
├── bot-library/              Shared bot files (*.json) that users can download
├── scripts/                  Install scripts (install-mac.sh + a double-click .command; install-windows.ps1 + a .bat) and developer tools (publish_public.py: a release to the public repo)
├── packaging/                Building the Mac .app and Windows .exe; app icons
│
└── docs/
    ├── README.md             Index of all documentation
    ├── roadmap.md
    ├── journal.md
    ├── decisions/            One file per decision (NNNN-short-title.md)
    ├── superpowers/          Design specs (specs/YYYY-MM-DD-topic-design.md) and their build plans (plans/), written before a big piece of work
    ├── guide/                For users: installing and using the app. Also shown at /help; the wheel bundles it as optionsjourney/guide
    └── developer/            For developers: how the code works (this file)
```

Folders are created when their first file is written, not before. `bot-library/` and `packaging/` don't exist yet.

The database itself is **not** in the repo. It lives in the user's data folder (on Mac, `~/Library/Application Support/OptionsJourney/`); see [getting-started.md](getting-started.md).

## Rules

1. **The root holds only standard project files** (the ones listed above). Code goes in `src/`, tools in `scripts/`, docs in `docs/`.
2. **No one-off scripts or notes in the repo.** Debug scripts, data fixes and "what I fixed" notes live outside the repo. If an explanation is worth keeping, it belongs in the journal or a decision.
3. **Dependencies flow one way.** `web` and `launcher` → `services` → `core`, `trading`, `bots`, `brokers`, `db`. `brokers` may use `trading` to read orders back. `trading` may use `core` (it reads market sessions from `core.sessions`); `core` imports none of the others. That keeps the trading logic easy to test and reuse.
4. **Each piece of logic lives in exactly one place.** For example, there is one P/L calculation and one open/close matcher, both in `core/`, and everything else calls them.
5. **Tests mirror the code.** The test for `src/optionsjourney/core/pnl.py` is `tests/core/test_pnl.py`.
6. **Naming:**
   - Python files are `snake_case.py`.
   - Docs and bot files are `kebab-case`.
   - Decisions are `NNNN-kebab-title.md`.
7. **No secrets or personal data in the repo.** This covers `.env`, tokens, databases, account numbers and home network addresses. `.gitignore` covers the common cases.
