# 0016. Group fills by order, with opens and closes apart

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
A broker reports every leg of an order, and every partial fill, as its own transaction. A 4-leg iron condor shows up as four or more rows, which makes the transaction list hard to read and gives a trade nothing to be categorized by. The author suggested grouping them.

## Decision
- **Group by order:** fills are grouped by **broker order ID**. Each broker transaction has its own ID; the order ID is what the legs of one order share.
- **Opens and closes apart:** within an order, opens and closes are separate groups. A roll shows as two rows: what it closed, and what it opened.
- **Expirations, assignments and exercises:** these carry no order ID, so they're grouped by account, time and underlying. A position's legs expire together.
- **Legs:** partial fills of the same instrument add up into one leg.
- **Default name:** each group gets a default trade name from its structure ([0004](0004-automation-first-strategy-naming.md)), e.g. "SPX Iron Condor" or "SPX Put Credit Spread".
- **Closing orders are named after the position they close.** Buying back a put credit spread reads "Put Credit Spread · Close", not as the debit spread its fills technically form.

## How structures are recognized (`core/structures.py`)
Recognition looks at what the legs actually are (put/call, strike, expiration, long/short), not just how many there are. Tracker2 called every 4-leg trade an iron condor.

| Shape | Names |
| --- | --- |
| 1 leg | Long/Short Put, Long/Short Call, Long/Short Stock |
| 2 legs | Put/Call Credit or Debit Spread, Ratio Spread, Calendar, Diagonal, Straddle, Strangle, Covered Call, Protective Put |
| 3 legs | Put/Call Butterfly, Broken Wing Butterfly |
| 4 legs | Iron Condor, Iron Butterfly, Reverse Iron Condor/Butterfly, Double Calendar, Double Diagonal |
| Anything else | Custom (N legs) |

## Consequences
- **One order is the building block for positions.** In the next step an opening group is matched with its closing groups, giving a trade's full life and P/L.
- **Some trades won't group cleanly.** A broker that doesn't report an order ID shows one group per fill. The "Individual fills" view stays available to check the raw detail.
