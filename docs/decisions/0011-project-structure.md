# 0011. A clean, conventional project structure

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
The author wants the repository to look and stay organized like a professional application. Tracker2 shows what happens without rules. Its root folder filled up with one-off scripts (`check_trade.py`, `debug_matching.py`, …) and fix-notes (`CRITICAL_BUG_FIX_SUMMARY.md`, `SCHWAB_QUICK_FIX.md`, …). Business logic spread across about 40 route files and a large `services/` folder.

## Decision
Follow standard Python conventions:
- **A `src/` layout** with one installable package, `src/optionsjourney/`, and all dependencies and tool settings in a single `pyproject.toml`.
- **Clear layers:** `core/` (pure trading logic), `brokers/`, `bots/`, `db/`, `services/` (which ties them together), `web/` and `launcher/`.
- **Supporting folders:** tests that mirror the code, plus `bot-library/`, `scripts/`, `packaging/` and `docs/`.
- **Root:** only standard project files.

The full layout and the rules are in [developer/project-structure.md](../developer/project-structure.md).

## Why
- **It's familiar:** developers recognize this layout straight away.
- **It's easier to test and reuse:** keeping trading logic free of web and broker code makes it simple to test, and makes it reusable by both the bots and the pages.
- **It stays clean:** written rules keep the repo tidy as it grows, instead of needing a cleanup later.

## Consequences
- **Code from Tracker2 gets reorganized as it's brought over,** not copied file for file.
- **Every new folder is documented** in project-structure.md.
