# 0092. Stocks performance comes from daily saves

- **Status:** Superseded by 0113 (chart and daily save); the ticker drawer still stands
- **Date:** 2026-10-21

## Context
The Stocks tab showed today's holdings only. Tracker2 charts a stock's price with the user's cost and trades, and the author wanted the same for total stock P/L over time. Brokers report today's positions and cost, not what was held on an earlier day, and rebuilding it from trades fails for shares transferred in or bought before the first sync. Issue #236.

## Decision
- A background job (`services/stock_history`, started like P/L Day's snapshots) saves every account's stock positions to `stock_snapshots` after each close plus 15 minutes (`SNAPSHOT_AFTER`; 4:15 PM, or 1:15 PM on early-close days). It looks every 10 minutes.
- Nothing is written until every connected broker answers (`broker_positions(strict=True)`), so a partial day never lands. There is no cut-off for that wait.
- The close is normally the official close, or else the regular session's last price that day (`expiry_prices.prelim_prices`). If a held ticker has no price the day waits, and at 8 PM New York time (`GIVE_UP_AT`) it is saved with that close empty; the chart leaves those shares out and says how many were priced.
- The Stocks tab charts P/L against cost from the saves (`core/stock_history.portfolio_series`), following the account picker, with ranges 1M, 3M, 1Y and All. The line starts on the first save; days the app was off stay missing.
- A ticker opens a drawer (`GET /api/stocks/<path:ticker>/chart`): daily closes from Schwab, else TastyTrade; a yellow dashed line at the broker's average cost; and buy and sell marks from synced stock fills, including shares from assignment or exercise (`trade_marks`). Shares from before the first sync or transferred in have no mark.

## Why
- Saving what the broker said at each close is the only record of past holdings that is always right, whatever the shares' history.
- Waiting for every broker keeps the line from jumping when one account is missing.
- Reusing the app's own "day's close" lookup keeps one place that decides what a close is.

## Consequences
- The history can't be backfilled: days before the first save, and days the app was off, are missing for good.
- A new table (migration 0050) and a new background thread.
- A day with a missing price is saved only at 8 PM, so the chart can lag that evening.
- A broker that stays down means no save that day, with no cut-off.
