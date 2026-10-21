# 0091. After hours, an option with no market is priced at its last real mark

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
After the bell, index weeklies lose their market: makers pull or widen their bids (0.00 × 20.40, 4.60 × 10.00). The journal marked every leg at the mid of bid and ask, so open P/L and P/L Day swung all evening and before the open on prices nobody could trade at. Worse, the P/L Day snapshot, taken from 4:15 PM, could save such a mark, and that becomes the next day's P/L Day baseline. Tracker2 fixed the same thing on 20 Oct (3440321e). Issue #241.

## Decision
- **No market:** an option whose ask is at least 0.50 and whose bid is missing or under half the ask (Tracker2's rule). Under 0.50 a bid-less quote is a worthless wing, where the mid is close enough. Stocks, indexes and futures are left alone.
- **Outside 09:30 to 4:15 PM New York** (or a holiday or weekend), such a quote is priced at the option's **last real mark**: the last mark the app saw from a quote that wasn't "no market". A real two-sided overnight quote stays live. Inside the session nothing changes.
- **Where:** the journal's open P/L, P/L Day, the Paper journal and the P/L Day snapshot. Bots, alerts, the risk graph and the Trade page keep reading the quote as it is.
- **The snapshot never stores a pulled market.** A trade with such a leg and no last real mark isn't snapshotted; P/L Day then falls back to the broker's previous close, as when the app was off.
- **Kept across restarts.** Tracker2 runs all day on a server; this app often doesn't. The snapshot saves the open legs' last real marks in the database (`close_marks`), and they're loaded when the app starts, so opening the app in the evening or before the open still prices pulled markets at the close.

## Why
- P/L shown after hours matches what the trades were worth at the close, not a quote that isn't a market.
- One rule in `core/quotes` used by every journal number, so the journal, P/L Day and the snapshot agree.

## Consequences
- With no last real mark (an option first quoted after hours on a fresh install), the page keeps the mid as before.
- A last real mark can be from earlier than the close if the option's market was already pulled before 4:15 PM.
