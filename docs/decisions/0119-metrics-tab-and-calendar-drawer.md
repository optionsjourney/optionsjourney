# 0119. Metrics gets its own tab; the P/L calendar opens a day, week or month

- **Status:** Accepted
- **Date:** 2026-10-25

Issue #301.

## Context
The P/L calendar and trading metrics were two of the Journal's four views (Trades, P/L, Trading metrics, Paper trades). Tracker2 gives them their own pages in its top bar. Its calendar opens a drawer for a day, a week or a month: P/L tables by category and by strategy, one column per account, plus the day's economic events and an economic-event marker on each day. Its metrics chart draws a cumulative P/L line over the P/L bars. This app has neither categories nor strategies. Users group trades with tags and save the views they care about as dashboards.

## Decision
- **A Metrics tab** in the top bar, next to Journal, holds **P/L Calendar** (`/metrics/calendar`) and **Trading metrics** (`/metrics`). They keep the Journal's filter bar, dashboard tabs and Save view. The Journal keeps Trades and Paper trades, and Stocks stays a Journal tab (it's holdings). `/journal/pl` and `/journal/metrics` redirect with their query.
- **The calendar drawer** opens from a day, a week's total cell, or the month's summary.
  - It shows the totals, then a table: one row per dashboard with trades in the period, then **Other trades**; a column for each account with P/L in the period, then the total.
  - Below the table, the trades under each dashboard, each with its account and the P/L it booked in the period.
  - A day also shows its economic events.
- **Dashboards stand in for Tracker2's categories.** A dashboard picks by its tags, accounts and search. Its saved status and dates don't apply, because the clicked period is the date. Dashboards can overlap, so the Total row and column count each trade once, and a note says when the rows add up to more.
- **The drawer adds up to the cell clicked.** It books P/L by the same rule as the calendar (`core/calendar.realized_between` next to `daily_pl`). A week is its days inside the month, weekend included, as the week cell is (Tracker2's drawer fetches the whole Monday–Friday, so it can differ from its cell).
- **Day cells carry an economic-event marker,** as in Tracker2: the first high-impact release with its time, a count of the rest, and every event in the tooltip.
- **Trading metrics' P/L bars carry a cumulative line** on a right-hand scale, with zero at the same height on both scales (Tracker2's combo chart), drawn in the app's own SVG chart.

## Why
- Metrics are read differently from the trade list, and Tracker2 has them on their own pages. Keeping the filter bar and dashboards means nothing is lost by the move.
- Dashboards are the groups users already made for themselves, so they answer "how did my 0DTE trades do this week" without a new setting.
- The drawer's cells are plain numbers rather than links to the matching trades: the journal's search finds one order at a time, and its dates filter by opening day, not by when P/L was booked. Each trade in the list links to its own page instead.

## Not done
Tracker2's per-day notes (a title and text stored for each day) would need a new table. Add them if they're asked for.
