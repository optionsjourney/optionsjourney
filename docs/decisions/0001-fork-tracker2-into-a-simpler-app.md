# 0001. Build a simpler app based on Tracker2

- **Status:** Accepted, partly replaced by [0021](0021-product-scope.md)
- **Date:** 2026-10-09

## Context
Tracker2 is the author's personal trading app, built up over about 2,000 commits. It syncs trades from Schwab and TastyTrade, builds them into positions, calculates P/L, and runs trading bots. Friends wanted to use it, but it was only ever built for one person:

- **No logins or users:** it has no login screen and no idea of separate users.
- **One person's setup:** it assumes the author's 7 accounts and his own named strategies (BIC, MEIC, METF, …).
- **Personal infrastructure:** it runs on two Macs with a dedicated server, PostgreSQL, Discord and Telegram alerts, and scheduled background jobs.
- **Far more than friends need:** about 40 feature areas, including an IV monitor, a wheel scanner, morning briefs and an analyzer.

## Decision
Start a new app, OptionsJourney, in a fresh repository. Use Tracker2 as a **reference**, not as a direct fork: bring over the proven core, and leave out everything personal or niche.

**Bring over:**
- the transaction → position → P/L pipeline
- the trade journal, dashboard and P/L calendar pages
- live P/L
- the bot engine
- the TastyTrade and Schwab connections
- the look and layout

**Leave out:**
- Discord and Telegram alerts
- the prod/dev server machinery
- the IV monitor, wheel scanner, analyzer, modeler and risk tools
- morning briefs and news
- the rule-based strategy matcher (see [0004](0004-automation-first-strategy-naming.md))
- one-off scripts and historical docs

## Why
- **A fork carries the baggage.** Forking the full repo would bring all of the personal setup and years of history along with it.
- **A blank-slate rewrite throws work away.** Hard-won logic like matching opening trades to closing trades, P/L, and iron condor splitting would have to be rewritten and debugged again.
- **A fresh repo that copies proven pieces gets the best of both.** It's clean, and it keeps the parts already tested on real trades.

## Consequences
- Each piece brought over gets cleaned of Tracker2's personal assumptions as it moves.
- OptionsJourney and Tracker2 will drift apart. Fixes in one won't flow to the other automatically.
- Tracker2 is kept as a read-only reference clone outside this repo, so none of its private history or settings ends up here.
