# 0026. The Stocks tab shows the brokers' holdings

- **Status:** Accepted
- **Date:** 2026-10-11
- **Replaces:** the Stocks tab part of [0025](0025-strategy-tags-and-stocks.md). The Stocks tag and strategy tags stand.
- **Also:** every strategy now always has a tag of its name (migration 0009). Before, a tag appeared only once a trade was given that strategy, so the author saw none.

## Context
[0025](0025-strategy-tags-and-stocks.md) made the Stocks tab the journal filtered to stock trades, with the usual summary boxes. The author asked for it to show just ticker, quantity, cost basis, current price, P/L and today's P/L.

The journal can't give those numbers. It knows only shares bought within the synced history (a year), so it misses older purchases and transfers in. On the author's data:
- AAPL showed as 0.09 shares at $0 cost: only reinvested dividends. The broker says 25.18 shares.
- AMZN didn't appear at all.

## Decision
- **The Stocks tab is a holdings table:** ticker, quantity, cost basis (per share, with the total), current price, P/L ($ and %), and today's P/L. It has one row per ticker, added up across accounts.
- **Quantities and cost basis come from the brokers' own positions:**
  - TastyTrade: `/accounts/{n}/positions`, average open price.
  - Schwab: `/accounts?fields=positions`, average price, for stocks and ETFs.
  - Positions are kept a minute.
- **Prices are live** from the quote hub. **Today's P/L** is the quantity × (price − previous close). The previous close comes from the brokers' quotes as on the watchlist, falling back to TastyTrade's own close price.
- **Only the account filter applies** on the Stocks tab. Stock trades still appear in **All trades**, with the Stocks tag.

## Why
- The broker is the only source that knows every share and its real cost basis.
- Measuring today's P/L from the previous close matches what the brokers show.
- Known gap: shares bought today are still measured from the previous close, not the price paid.
