# 0070. Split condors with uneven sides, and split them on Match trades

- **Status:** Accepted
- **Date:** 2026-10-17

## Context
Splitting a condor into a put spread and a call spread needed the same number of contracts on every leg, so an ERN-style bot (1 put spread, 3 call spreads) got no **Split iron condors** switch and its journal trades had no Split button (#166). Tracker2 splits any four legs with two puts and two calls whatever the quantities (`transaction_processor`, `detector.split_0dte_iron_condor`). Match trades (decision 0068) filed a whole condor whole even when the bot splits its condors.

## Decision
- One rule for a trade and a bot: it sells a put and a call and buys a put and a call, opened together, all at the same expiration. Quantities may differ. Different expirations (double calendars, diagonals) don't split.
- Trade: `core.structures.splits_by_type` on the opening order (replaces the list of iron shape names). Bot: `definition.is_iron_condor_shape`, all four legs at the same DTE, no group mixing puts and calls (decision 0069).
- Match trades: when the bot splits its condors, a ticked whole condor is split as the Journal's Split button does (`journal.set_split`) and both halves (`<key>#put`, `<key>#call`) are filed. Undo puts back the trade's earlier split choice, so it shows combined again.

## Why
- **Follow Tracker2.** The user asked for one plain rule.

## Consequences
- A trade's halves keep their own units (1 and 3) and price per unit.
- A preset iron butterfly bot is still one group mixing puts and calls, so it has no switch; its journal trades can still be split by hand.
