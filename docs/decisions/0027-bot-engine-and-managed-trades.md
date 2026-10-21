# 0027. The whole bot engine comes over; stops and exits work on any trade

- **Status:** Accepted
- **Date:** 2026-10-11

## Context
Tracker2's bot engine is about 35,000 lines. Turning fills into trades doesn't need it, and that part is already built here. But it owns much of how trades are looked after:
- stops, profit targets, re-arming and timed closes
- fixing fills the broker labels backwards
- exact strategy names and close reasons
- the shared broker order code

In Tracker2, only trades a bot opened get stops and exits. A hand-opened trade has to be "adopted" into a bot, which creates a bot run behind the scenes.

## Decision
- **All of it comes over,** in five pieces, each with its own spec, plan and build:
  1. the order path
  2. the bot-to-trade link
  3. trade management
  4. bot entries
  5. bot files and the library
- **A managed trade is its own thing,** separate from bots. A bot creates one when it opens a trade, and the user can turn management on for a trade they opened by hand. One exit engine serves both.
- The aim is Tracker2's behavior, not its code line for line: rebuilt on this app's layers, with no personal settings.

## Why
- The author's trading depends on the whole engine, exits included.
- Friends who trade by hand get stops too, without a fake bot run.
- Turned down: **bot trades only, like Tracker2** (it keeps the adopt workaround and leaves manual trades unprotected), and **only part of the engine** (the author needs all of it).

## Consequences
- Phase 6 is large. The pieces are built in order, because each depends on the one before.
- Piece 2 has to design managed trades so that they don't require a bot.
