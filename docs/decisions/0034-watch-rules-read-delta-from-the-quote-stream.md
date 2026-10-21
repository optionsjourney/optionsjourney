# 0034. Watch rules read delta from the quote stream

- **Status:** Proposed
- **Date:** 2026-10-13
- **Builds on:** [0021](0021-product-scope.md) (live quotes from the brokers' streams), [0030](0030-bots-built-as-thin-slices-in-tracker2s-tables.md) (bots follow Tracker2)

## Context
Bots slice 7 adds watch rules: close a trade on its position delta, its short/long ratio, the underlying's move or a short being tested. The ratio and the underlying need only prices, which the quote stream already has. Delta needs each option's delta, fresh, every few seconds. Until now the app read deltas only once, from the option chain, when it picked strikes. Tracker2 subscribes TastyTrade's DXLink `Greeks` event and trusts a delta for 150 seconds, because DXLink sends Greeks only every 60–95 seconds.

## Decision
- The TastyTrade quote stream also asks DXLink for `Greeks` (the `delta` field) on every option symbol it streams.
- A delta is kept apart from the option's quote, with its own time. A Greeks event never makes the quote look fresh, and a quote never makes a delta look fresh.
- A quote also keeps the time of its last trade price (`last_at`), so the underlying's price is judged fresh by when it traded, not by the last bid or ask update.
- Watch rules trust a delta for 150 seconds and a price for 30, and only if it arrived after the trade filled. With no fresh delta, the delta rule waits and alerts; it never closes anything.
- Schwab's stream isn't used for delta: bots trade TastyTrade only.

## Why
- It's what Tracker2 does, and it gives the same delta the broker's own platform shows.
- Working delta out locally (Black–Scholes from the mid) would add a second source of truth that could disagree with the broker's, and needs an implied volatility the app doesn't have per option.
- Turned down: **polling the option chain** every few seconds for deltas. It would hit the broker's chain endpoint far too often.

## Consequences
- The stream carries a little more data: one Greeks event per option every minute or so, for every option the app streams.
- The field names haven't been checked against a live DXLink feed yet. Until they are, a mistake shows up as "no fresh delta" (the rule waits and alerts), never as a wrong close. A read-only check against a live chain should come before the first live use of a delta rule.
