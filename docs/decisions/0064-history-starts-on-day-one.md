# 0064. Trade history starts the day the app is first used

- **Status:** Accepted
- **Date:** 2026-10-16

## Context
The first sync reached back a year. That brings in hundreds of past trades with no strategy or tags, which a new user would have to sort by hand before the journal and bot stats mean anything. On the author's development database it also left a year of trades that predate the imported bots (#148).

## Decision
History starts on the day of the first sync. That day is saved as the `history_starts` setting. Sync never fetches or adds a transaction dated before it; transactions already stored from before it still update.

A development database can be trimmed to a later start: the trades kept are the ones opened on or after the date, closed on or after it, or still open, each with every transaction back to its opening. Everything else from before the date is deleted (after a backup), and `history_starts` is set to the date.

## Why
- **No sorting chore on day one.** Every trade in the journal happened while the app, and its bots, were in use.
- **One floor, in one place.** `sync.history_starts` decides it; trimmed history never comes back on the next sync's 7-day overlap.

## Consequences
- **Positions already open on day one have no opening.** Their closes show up without the trade they belong to. Importing past history is a possible later option, with a clear warning first that past trades must be sorted by hand.
- Daily account values still reach back from the broker; they don't need sorting.
