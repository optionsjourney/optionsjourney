# 0040. Bots can trade legs in other expirations and hold trades overnight

- **Status:** Accepted (the author's rulings on the far legs and the timed close, 2026-10-13)
- **Date:** 2026-10-13
- **Replaces:** the part of [0036](0036-new-entry-styles-only-where-the-exits-can-protect-them.md) that refuses legs in other expirations and trades held overnight, and the "every shape stays same-day" line of [0039](0039-any-same-day-structure-with-a-stop-that-always-works.md)
- **Builds on:** [0039](0039-any-same-day-structure-with-a-stop-that-always-works.md) (any structure), [0037](0037-schwab-bots-stops-are-watched-by-the-app.md) (stops watched by the app), [0030](0030-bots-built-as-thin-slices-in-tracker2s-tables.md) (bots follow Tracker2)

## Context
Decision 0036 kept every bot same-day until the exits could protect a trade across the session change. Tracker2 trades calendars, diagonals and multi-day shapes: each leg has its own DTE, resolved to a listed expiration (`chain.resolve_leg_expirations`), and its resting stops are GTC. Slice 14 (#96) brings that over. This app's exits were written for one expiration that ends today: a group's stop rests on its short, and when that short expires the rest of the group had nothing to manage it.

## Decision
- A leg can be **0 to 60 days to expiration**, resolved exactly or to the next listed expiration (never past 60 days; DTE 0 always exact) (Tracker2's `use_exact_dte`). Each leg is picked on its own expiration's chain, and the entry is one order across them.
- In each group, **no sold leg expires after a bought leg**. Calendars, diagonals and their doubles fit; reverse calendars are refused.
- A trade is **held overnight** with the protections it has during the day: resting stops are GTC and stay at the broker; targets, watched stops and watch rules carry on at the next session.
- **A calendar or diagonal must have a timed close.** A bot with any group in more than one expiration can't be saved without one. It counts days to the group's nearest leg, so it closes the group on the near legs' last day at the latest. In the author's words: "In practice, we should never have any expirations on double calendars."
- If the near legs expire anyway (the timed close didn't fill), the group's stop and target end, and the **far legs are left to you**, as Tracker2 leaves them. The bot never sells them. An **urgent safety alert** goes to every channel (decision 0035), and the integrity check and the pre-open check keep saying so.
- At **09:20** on each trading day, and at startup, the app **checks every held trade against the broker** and alerts what doesn't match. It changes nothing.
- **Schwab bots stay same-day**, and handing over a trade stays same-day.

## Why
- The author wants bots to trade calendars, diagonals and trades held for days, as Tracker2 does.
- Requiring sold legs to expire first means what's left after an expiry can lose no more than it cost.
- Requiring a timed close means a calendar never reaches its near expiration in normal running. The far-leg backstop follows Tracker2 and the author: the bot doesn't trade what it can no longer price as a group.
- Tracker2's 09:20 sync exists because its exits read the journal. Ours read the broker, so the useful part is the check, not the sync.
- Turned down:
  - **Selling the far legs at the next open** (built first, then removed on the author's answer). It's an automated market-hours sale of options the user may want to keep.
  - **GTC Overnight exits.** This app has no resting limit exits for it to apply to.
  - **No timed close for calendars**, as Tracker2 allows. The author wants no calendar ever to reach its near expiration.

## Consequences
- Migration 0028 adds `bots.use_exact_dte` (on). The bot file shares it and stays version 2; an older app refuses a leg above 0 DTE by its own validation.
- A new exit kind, `settle` (state `left`), records the far legs left to you. Far legs left keep the trade counted toward Max open trades until they expire.
- The checks that look back over open trades read 70 days, not 10.
- A trade whose stops the app watches (any debit bot, so every calendar) is protected overnight only by what rests at the broker, which for those is nothing: the app must be running at the next open. The pages and the pre-open alert say so.
- Existing same-day bots trade and are protected as before.
