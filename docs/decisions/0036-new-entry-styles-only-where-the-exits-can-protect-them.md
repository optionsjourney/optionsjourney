# 0036. A new entry style ships only where the exits already protect it

- **Status:** Proposed
- **Date:** 2026-10-13
- **Builds on:** [0027](0027-bot-engine-and-managed-trades.md) (the bot engine), [0030](0030-bots-built-as-thin-slices-in-tracker2s-tables.md) (bots follow Tracker2), [0009](0009-bot-safety-defaults.md) (bot safety defaults)

## Context
Tracker2's bots can open many shapes: strikes by premium, delta, spot offset or parent offset; leg ratios above 1; sizing by a dollar amount; legs in different expirations (calendars, diagonals) held overnight. This app's exits (stops, targets, the wing after a stop, timed closes, time actions, watch rules, profit actions, recovery and the integrity checks) were built and tested for one shape: two sides, each one short and one long of the same type, one for one, expiring the same day. Bots slice 10 ("more entry styles", #86) has to decide how much of Tracker2's entry side to bring over.

## Decision
- A new way to **choose strikes or when to enter** ships when what it opens is still that one shape: strikes by delta, by spot offset, and a floating entry window are in.
- A new **shape** ships only together with exits that handle it, tested end to end. Leg ratios above 1, legs in other expirations, and trades held overnight stay refused by validation until then.
- A strike pick that needs market data (deltas, a fresh SPX price) **fails closed**: missing or old data skips the entry and alerts. It never falls back to another way of picking.
- Fixed-dollar sizing waits: Tracker2 refuses it for credit entries, and these bots only open for a credit.

## Why
- An entry the exits can't protect is the most expensive mistake a bot can make: a 1×2 side's extra long left after a stop, or a GTC stop that lapses overnight on a calendar's far leg.
- Picking a strike differently changes nothing after the fill, so it's safe to add on its own.
- Tracker2 fails a delta pick when its chain has no deltas rather than picking by price (`chain.chain_has_deltas`); this extends that to partial and stale data.
- Turned down: **bringing all of Tracker2's entry styles over at once** with the shapes refused only at run time. The form would offer settings that can never trade.

## Consequences
- Validation still refuses ratios above 1 and any DTE but 0; the guide says these are coming.
- A later slice that adds a shape must change the exits first and show, with tests, that stops, targets, the timed close, time actions, watch rules, profit actions, recovery and the integrity checks are right for it.
