# 0032. Economic events and the market calendar come from public calendars

- **Status:** Proposed (built in bots slice 5, waits on the author's review)
- **Date:** 2026-10-13
- **Settles:** #29 and the "Economic events" question left open in [0021](0021-product-scope.md)

## Context
Tracker2's bots can skip, or only trade, FOMC and CPI days and the last trading day of each month, and their holiday calendar comes from NYSE's published page. This app had neither: the bots could only use typed dates, and the holiday table was hard-coded for 2026–2027. Tracker2 reads the dates from ForexFactory's weekly export (its Sunday-night pull), the Federal Reserve's FOMC page, FRED (with the author's API key) and nyse.com. The spec is 2026-10-13-bots-slice-5-market-filters-design.

## Decision
- **Keep the check** and take the dates from the same public sources as Tracker2, with Tracker2's rules for which one decides a day:
  - **ForexFactory** (`nfs.faireconomy.media/ff_calendar_thisweek.json`) for the week it has refreshed into;
  - the **Fed's FOMC calendar** for FOMC days, and **FRED** for CPI days, while refreshed within 21 days;
  - a day is in a list when **any** fresh source has it (Tracker2 lets ForexFactory alone decide its week; here a renamed ForexFactory title can't drop a Fed or FRED day), and a Fed or FRED day ForexFactory lacks is alerted;
  - **end of month** worked out from the calendar;
  - **NYSE's hours page** for closures and early closes, accepted only if, for 2026–2027 (the years the built-in table covers), its closures and early closes are exactly the table's; for later years, at least 9 closures and an early close a year; and it adds no closure in the next 7 days that the calendar in use doesn't have. Accepted, it replaces the table for the years it covers; otherwise the table stays and the app alerts.
- **FRED is optional.** Its key is a field on the Settings page; without it, CPI days come from ForexFactory's current week, which is what a bot needs on the day.
- **The app fetches on its own** while it runs: ForexFactory each new week (from Sunday 18:00 New York, when Tracker2 syncs), the others weekly, a failed source again after an hour, plus **Refresh now** on the Automation page. Requests are plain HTTPS GETs; nothing about the user is sent.
- **Bad data never replaces good data.** Each source is checked before it's stored (no weekend dates, enough meetings or holidays, no stored future date missing); a failure keeps the old rows and alerts.
- **A list the app can't read fails closed for blackouts:** a bot that blacks out FOMC days doesn't enter on a day the app can't tell, and alerts. Tracker2 treats an unreadable list as empty; this is the spec's question 1 for the author.

## Why
- These are the sources Tracker2 has used in production, with parsers and checks already worked out; following them keeps the two apps' bots skipping the same days.
- Friends won't have a FRED key, and the bots only ask about today, which ForexFactory covers; FRED stays available for the look-ahead.
- A local app may be closed on Sunday evening, so "when due" checks replace Tracker2's fixed weekly schedule.
- Turned down:
  - **Dropping the check:** loses a filter the author's strategies use.
  - **Yahoo, FinancialJuice, Finnhub, bls.gov:** Tracker2 tried them; no impact rating, no supported feed, refused, or blocked (its spec's "Rejected sources").
  - **Hard-coding the dates:** goes stale, and needs an app update for every reschedule.

## Consequences
- The app makes a few outbound requests a week to federalreserve.gov, nyse.com, ForexFactory's export and (with a key) FRED.
- New tables: `economic_events`, `economic_event_sources`, `market_calendar` (migration 0019).
- If a site changes its page, that source's refresh fails loudly, keeps the old dates and alerts; the parser needs updating.
- Showing the events on the calendar and journal pages (Tracker2's display) is left for later.
