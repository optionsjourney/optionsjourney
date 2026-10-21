# 0020. Position matching: lessons from a year of real history

- **Status:** Accepted (amends [0017](0017-positions-follow-tracker2.md))
- **Date:** 2026-10-09

## Context
The first time the journal ran over the author's full year of history (about 5,800 trades), it crashed. Once the crash was fixed it showed 2,293 trades stuck as "partial" and 713 broker records it couldn't place. Tracing them turned up patterns that [0017](0017-positions-follow-tracker2.md)'s rules didn't handle:

1. **One expiration record covers many positions.** At the close, the broker reports everything expiring in an account as one batch: all the day's iron condors at once. "One record closes one position" matched none of them.
2. **Cash-settled assignments and exercises come as two rows with no direction.** For SPX, for example, TastyTrade posts "Removal of option due to assignment" and "Cash settlement of …" for each contract, with neither marked as a buy or a sell.
3. **One order can close legs of several positions.** For example, one order sold 2 calls that were the long wings of two separate iron condors. Tracker2 leaves these for the user to match by hand.
4. **Brokers post corrections.** Schwab posted an open and a close for the same contract in the same second.
5. **Some broker events aren't trades,** e.g. a corporate action on shares. Treating one as a close over-closed a stock position and crashed the journal.

## Decision
- **Expirations, assignments and exercises are settled leg by leg.** Each contract's quantity settles the oldest open positions holding it, shared across several positions if needed.
- **Missing directions are implied:** only a short option is assigned, and only a long option is exercised. A contract's removal row and cash row are combined, so the quantity is closed once and the cash counts once.
- **A closing order that no single position fits may close legs of several,** oldest first, **but only if every contract in it is accounted for.** Otherwise it stays unmatched. Tracker2's one-order-one-position rule still comes first.
- **Opens come before closes at the same moment,** so a broker's correction pair nets out.
- **Unrecognized broker events are never applied.** They're listed with the unmatched records for the user to see.
- **A close is applied only if every fill fits:** the right contract, the right direction, and no more than is left. Nothing is ever over-closed.
- **One bad record can't take down the journal.** If a record still fails, it's set aside, logged and counted on the page.

## Result on the author's history
- **Before:** 2,626 closed, 2,293 partial and 850 open trades; 713 records not placed; positions left open after their options expired.
- **After:** 5,616 closed, 17 partial and 135 open trades; 38 records not placed (22 closes of trades opened before the synced year, 16 non-trade broker events); **no position left open after its options expired.**

## Consequences
- **One more departure from Tracker2:** this is a fourth place where we differ from Tracker2, in the direction of fewer trades needing manual matching.
- **Tests:** every pattern above has a regression test in `tests/core/test_positions.py`.
