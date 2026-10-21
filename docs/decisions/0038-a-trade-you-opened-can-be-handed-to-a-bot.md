# 0038. A trade you opened can be handed to a bot

- **Status:** Proposed
- **Date:** 2026-10-13
- **Builds on:** [0027](0027-bot-engine-and-managed-trades.md) (the bot engine), [0028](0028-one-order-path-and-two-pause-switches.md) (one order path), [0030](0030-bots-built-as-thin-slices-in-tracker2s-tables.md) (bots follow Tracker2), [0036](0036-new-entry-styles-only-where-the-exits-can-protect-them.md) (only where the exits can protect it)

## Context
Until now a bot managed only the trades it opened. Tracker2 can "adopt" a trade placed by hand: a bot takes it over and manages its exits as if it had opened it. In Tracker2 that's only for its Analyzer strategies, and it cancels the trade's working orders and replaces them with the bot's. This app has no Analyzer strategies; every bot is an SPX same-day iron condor. Bots slice 12 (#88) brings the hand-over here.

## Decision
- From a trade's journal page you can **hand an open iron condor, opened and expiring today, to a live bot on the same account whose shape matches** (4 legs in 2 sides, ratio 1, within its per-trade limit). The bot then manages it exactly as its own: stops, targets, closes, the journal link.
- **You see the exits first.** A check works out each side's stop and target the way a bot's own fill would, and checks every stop against the short's fresh ask. Confirming records the run and places those exits through the one order path.
- **It refuses rather than guesses:** a trade with working orders (nothing is cancelled for you), a stop that would fire at once or can't be checked, a broker that can't be read, either pause switch on, outside the regular session, or anything changed since the check.
- Rolls, futures bots, linked accounts and the OptionOmega comparison page stay out (the spec says why for each), and so does starting a bot-managed trade from the Trade page until the author chooses how it should look.

## Why
- A friend who opens a condor by hand still wants the bot's stops on it. Doing it by hand at the broker is where mistakes happen.
- Refusing working orders instead of cancelling them means the hand-over never takes away a protection you put there yourself; a cancel that half-works would leave the trade bare.
- Checking each stop against the live ask before anything is recorded is the same rule as moving a stop (slice 4), for the same reason: a stop below the market fires the moment it lands.
- Turned down: **Tracker2's cancel-and-replace** (more ways to end unprotected); **any day's trade** (our exits are built for same-day trades and expire at today's close); **a warning for a stop that would fire at once** (Tracker2's) instead of a refusal.

## Consequences
- An adopted run looks like a bot's own filled run, told apart by its key (`adopt-…`). No new tables.
- It counts toward the bot's open trades, not its entries for the day.
- Its underlying-move watch rule can't run (no SPX price was recorded when it was opened); the check says so.
