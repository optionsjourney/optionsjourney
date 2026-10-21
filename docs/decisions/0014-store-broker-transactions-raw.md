# 0014. Store broker transactions exactly as received

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
Everything in the journal starts from the transactions the brokers report. Tracker2 converts each one into its own columns as it fetches it, and keeps two near-identical tables, one per broker. A bug in that conversion is baked into the stored data, and fixing it means re-syncing.

## Decision
- **One table, raw data:** a single `broker_transactions` table holds every transaction **exactly as the broker sent it**, as JSON, for every broker. The record is keyed by broker and the broker's own transaction ID.
- **Conversion happens on read:** each broker module has a pure function that turns a raw record into **fills**, a common shape that's the same for every broker (`core/transactions.py`). The conversion runs whenever fills are needed. Nothing downstream ever reads broker JSON.
- **Rows change only when the broker's record does:** a stored row is replaced only if the broker's record itself has changed. TastyTrade, for example, finalizes estimated fees the day after a trade.
- **Every transaction type is kept,** including deposits, dividends and interest, even though the journal only uses trades for now.

## Why
- **Parsing fixes are free:** fixing or improving the conversion needs no re-sync. Every stored record is simply read again.
- **The broker is the source of truth:** we can always show what the broker actually said.
- **One pipeline:** the position builder and P/L only ever deal with fills.
- **Nothing to backfill later:** keeping money movements now means the dashboard and cash-flow features won't need another backfill.

## Consequences
- **Parsing runs on every page load.** That's fine at this app's scale (thousands of rows, not millions). If it ever isn't, fills can be cached.
- **Stored records can be unreadable.** A record the parser can't read is counted and reported on the page, never silently dropped.

## Syncing
- **Incremental:** each account syncs from where it left off, re-reading the last **7 days** so late changes are picked up.
- **First sync:** reaches back **1 year**.
- **Broker windows:** TastyTrade is read page by page. Schwab is read in 60-day windows, because it caps each request.
- **Failures stay contained:** one account failing doesn't stop the others. Its error is saved and shown.
