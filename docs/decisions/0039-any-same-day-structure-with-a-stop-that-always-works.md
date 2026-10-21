# 0039. A bot can open any same-day structure, and every stop it offers really works

- **Status:** Proposed
- **Date:** 2026-10-13
- **Builds on:** [0036](0036-new-entry-styles-only-where-the-exits-can-protect-them.md) (a new shape ships with exits that protect it), [0037](0037-schwab-bots-stops-are-watched-by-the-app.md) (stops watched by the app), [0030](0030-bots-built-as-thin-slices-in-tracker2s-tables.md) (bots follow Tracker2), [0009](0009-bot-safety-defaults.md) (bot safety defaults)

## Context
Until now a bot could only be an iron condor: two sides, each one short and one long of the same type, one for one, opened for a credit. The author wants bots to handle any kind of option trade. Tracker2 allows any legs (one or more, any ratios, buy or sell, credit or debit) in optional exit groups. But only a group with a single short leg can rest a stop at the broker, because brokers take only single-leg stop orders. Tracker2 records a % stop on any other group as "deferred", and nothing ever watches it, so those trades run with no working stop.

## Decision
- A bot can have **1 to 4 legs**. Four is the most a TastyTrade order can carry. Each leg has its own ratio from 1 to 10, can be a buy or a sell, and can be a call or a put. Every leg belongs to one of **1 to 4 exit groups**. A bot with one group is managed as one whole trade, which is Tracker2's "groups off".
- A bot says whether it **opens for a credit or a debit** (Tracker2's `premium_side`). An entry whose price comes out on the other side, or at zero, is skipped.
- **Every stop the form accepts works:**
  - A group with **exactly one short leg** rests a single-leg stop on that leg at the broker, as today. After it fills, the group's other legs are closed the way the group says.
  - **Any other group** has its stop watched by the app on the group's whole price, using the slice 11 watcher (decision 0037). Examples: no short (long options, debit spreads), or two or more shorts (a short straddle, or an iron butterfly kept as one group). The form says so on that group, and the bot page and arming confirmation say the app must stay running.
- **Naked short legs are allowed**, as in Tracker2. This was the author's choice on 2026-10-13. Stops stay optional, as for any bot.
- **Profit targets** come from the group's net entry price: a share of a credit kept, or a gain on a debit paid (Tracker2's `profit_target_price`).
- New bots start from a **structure picker** of common shapes. Tracker2 has none; this was the author's choice for users who aren't technical.
- **Contract limits** count a trade's largest leg (units × ratio), for bots as well as for you.
- Every shape stays **same-day** (0DTE). Legs in other expirations and trades held overnight come in slice 14 (#96).

## Why
- The author wants a bot to be any trade, not just the iron condor.
- Tracker2 is the reference, but copying its unwatched multi-leg % stop would leave trades unprotected without saying so. The app already has a tested watcher (slice 11), so every group can have a stop that does something.
- Four legs is the broker's limit for a single order, so allowing more would let a bot be saved that can never be sent.
- Turned down:
  - **Refusing stops on groups that can't rest one.** That would make long options, debit spreads and short straddles impossible to protect.
  - **Making the user split every group into single-leg groups**, which is Tracker2's UI advice. It's hard to understand, and it breaks a spread's combined close.
  - **No limit on legs**, because the broker can't send more than four.

## Consequences
- Decision 0036's refusal of ratios above 1 is lifted for same-day trades. Its refusal of other expirations stands until slice 14.
- Groups whose stop the app watches are protected only while the computer running the app is on, awake and connected. The pages say so.
- Every exit path is reworked for a group of any shape and tested per shape: stops, targets, the rest of a group after a stop, closing, timed close, time actions, watch rules, profit actions, managing from the journal, Schwab's watched stops, recovery and the integrity checks.
- Handing a trade you opened to a bot (slice 12) stays iron condor only. Any other bot shape is refused for hand-over with a plain reason.
- The bot file goes to **version 2**: it carries any shape (up to 4 legs and groups, ratios up to 10) and what the bot opens for (`opens_for`). Version 1 files still import, as iron condors that open for a credit. An older app refuses a version 2 file by its version check. Decision 0033 still describes version 1.
- Found while building: a stop rests at the broker only on a group with exactly one short leg **in a bot that opens for a credit**. Every group of a debit bot is watched by the app, because a debit spread's or long butterfly's short leg rises while the trade is winning, so a resting buy-stop on it would fire on a profit.
- Existing iron condor bots trade and rest their stops as before. Contract limits now count the largest leg (the same for ratio 1), and the pages say "group" where they said "side".
