# 0003. TastyTrade, Schwab and manual entry

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
Trades have to get into the app somehow. The options were automatic sync from TastyTrade, automatic sync from Schwab, uploading a CSV export, and typing trades in by hand.

## Decision
Support **TastyTrade auto-sync**, **Schwab auto-sync** and **manual entry**. CSV upload is not planned for now.

## Why
- **Auto-sync is what makes the journal effortless,** and the bots need a live broker connection anyway.
- **Manual entry covers everyone else:** anyone who can't or won't connect a broker, plus the odd trade a sync misses.
- **CSV import is extra work with little gain** for people who are already syncing.

## Consequences
- **Schwab is the hardest broker to connect.** Each person has to register their own app on Schwab's developer site and wait for Schwab to approve it, which can take days. After that, Schwab requires a fresh login every 7 days. The app needs a one-click "Reconnect Schwab" flow and a clear reminder when it's due.
- **TastyTrade is simpler:** create an OAuth app once, then paste in two values.
- **Starting point:** TastyTrade is the likely first broker to support end to end.
