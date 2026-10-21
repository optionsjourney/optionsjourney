# 0077. Trade popups, recorded from the journal after each sync

- **Status:** Accepted
- **Date:** 2026-10-19

## Context
A bot's stop filling only showed when you reloaded the Journal. Tracker2 shows a popup on any page when a trade opens, rolls, partly closes or closes, and lists them on its Alerts page (its 2026-09-29 transaction alerts design). Issue #178.

## Decision
- Follow Tracker2: popups for open, roll, partial close and close, for every trade in the journal (a bot's or your own), at the top right of every page, 10 s each, hover pauses, × closes, at most 5 then "+N more", one chime per burst. Fills older than 10 minutes when recorded are listed but don't pop up. Expirations aren't alerts.
- Unlike Tracker2, which records events inside its sync from its positions table, this app has no positions table: the journal is built from the transactions on read. So after any commit that stores transactions, the app loads the journal and records each trade's events from the last day that it hasn't stored yet, keyed by kind, trade and order ids. The alerts say exactly what the journal says.
- A **Trade popups** setting under Notifications (default on) turns the popups off; the Alerts page's Trades list keeps filling.

## Why
- One source of truth: reading the journal's own trades means no second matcher that could disagree with it.
- Recording after the commit, on its own thread, means an alert problem can never fail or slow a sync.

## Consequences
- A sync that stores transactions costs one journal build more (cached, so usually the build the next page load would do anyway).
- A trade's name and bot are kept as they were when the event was recorded. Changing a condor between split and combined records its halves (or the whole) again within the day.
- No backfill: the list starts from the first sync after the update, covering fills from the last day.
