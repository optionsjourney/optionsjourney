# 0007. Guided setup wizard

- **Status:** Accepted (details will change as we build it)
- **Date:** 2026-10-09

## Context
The people using this app aren't programmers. Connecting a broker's API means visiting developer sites, copying keys and completing a login. That's the hardest part of getting started.

## Decision
Setup happens in two steps:

1. **Install:** a tiny script, `setup.sh` on Mac or a double-click `Setup.command`, installs everything the app needs without touching the rest of the computer, then opens the app in the browser.
2. **Setup wizard:** a guided setup in the browser walks the user through:
   - **Brokers:** which they use (TastyTrade, Schwab, both, or manual only)
   - **Connecting TastyTrade:** with screenshots of where to find the keys, and an instant check that they work
   - **Connecting Schwab:** registering a developer app, waiting for Schwab's approval (this step can be skipped and finished later), then a one-click login
   - **Accounts:** which to track, and their nicknames
   - **Trading style:** a few questions that create starting categories for manual trades
   - **Bots:** optionally adding bots from the shared library
   - **Safety settings:** see [0009](0009-bot-safety-defaults.md)

The wizard can be re-run from Settings at any time, for example to add Schwab later.

## Why
- **Easier than a terminal:** a browser wizard can show pictures, check answers on the spot and let people come back later. Terminal questions can't.
- **The logins happen in a browser anyway,** so the wizard keeps everything in one place.

## Consequences
- **The screenshot guides will need upkeep** whenever TastyTrade or Schwab redesign their sites.
- **Credentials are stored in the operating system's secure storage** (the Mac Keychain or Windows Credential Manager), not in a plain text file.
- **The wizard has to handle "not finished yet" well,** because Schwab's approval can take days.
