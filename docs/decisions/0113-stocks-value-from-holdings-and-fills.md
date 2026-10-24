# 0113. The Stocks tab's value chart is worked back from today's holdings and the synced fills

- **Status:** Accepted
- **Date:** 2026-10-23
- **Replaces:** the chart and the daily save of [0092](0092-stocks-performance-from-daily-saves.md). Its ticker drawer still stands.

## Context
Decision 0092 charted total stock P/L against cost from daily saves of the brokers' holdings (`stock_snapshots`). Two things didn't hold up:
- **The line moved with every trade.** P/L against cost jumps whenever shares are bought, sold or assigned, so the chart mostly showed trading activity, not how the shares did.
- **It started empty.** History can't be backfilled from saves, so the chart filled in one day at a time from the first save, and days the app was off were missing for good.

The author also wanted the totals at a glance and one list of every stock trade. Issue #286.

## Decision
- **Three cards** at the top of the Stocks tab, from the live holdings the table already shows (`core/holdings.stock_summary`): Total P&L (unrealized, with % of cost), Total market value, and Day P&L (with % of the value at the previous close). They follow the account picker, refresh with the table every 5 seconds, and show "—" until a price arrives.
- **A Portfolio value chart:** the market value of the shares held at each trading day's close, from the first day of history (`history_starts`, decision 0064) to today. It is worked out, not saved:
  - **Quantities:** start from today's broker quantities (`broker_positions(strict=True)`) and undo each synced stock fill after the day (buys, sells, assignment and exercise), per account and ticker (`core/stock_history.share_moves`, `held_by_day`).
  - **Reconciliation:** a long that the fills would take below zero, or a short above it, is held at zero that day and logged, not shown.
  - **Prices:** each ticker's daily closes (Schwab, else TastyTrade), kept an hour in memory; a day with no close uses the latest one from up to 10 days before (`value_series`). Today's point is today's quantities at live prices once the market opens, else the last close.
  - **A broker that doesn't answer:** nothing is drawn, and the chart says so. No partial line.
  - It loads after the page (`GET /api/stocks/value`), and has no range buttons.
- **Tabs:** **Holdings** (the table, plus a Market value column) and **Transaction History** (every synced stock fill, newest first; a held ticker opens the drawer).
- **Removed:** the "Your stocks against what you paid" card, the daily stock save (`services/stock_history`) and the `stock_snapshots` table (dropped by migration 0054).

## Why
- Market value from quantities and closes only moves for a price change or a trade the user can see in Transaction History, so every step in the line has a visible reason.
- Working back from today's quantities counts shares held before the first sync, which a forward rebuild from trades would miss.
- Daily closes for finished days never change and the fills are already stored, so the history needs no table and no background job, and is there from the first visit.
- No ticker mapping and no split handling: a fill's ticker is matched to the broker's holding as both spell it, and nothing reads split events. Neither is in the data the app already has, and both cases are rare.

## Consequences
- **No history before `history_starts`:** the app has no fills from before then.
- **Splits aren't handled.** Fills keep their own day's share counts while closes are split-adjusted, so a stock that split after `history_starts` is wrong before the split. The guide says so.
- **A broker that is down means no chart** until it answers again; the cards and table still show.
- **Shares transferred in or out** without a fill make the walk-back disagree with the broker; the clamp hides impossible quantities but the earlier days can still be off for that ticker.
- One less table and background thread. Loading the chart reads every broker's positions and, on a cold cache, each ticker's closes.
