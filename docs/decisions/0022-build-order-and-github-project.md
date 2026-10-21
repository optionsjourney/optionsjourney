# 0022. Build order, tracked in a GitHub project

- **Status:** Accepted
- **Date:** 2026-10-10

## Context
[0021](0021-product-scope.md) set out everything the app will do. Several features share foundations: prices are needed by expired-trade pricing, live P/L, charts, Analyze, alerts and bots; settings and alerts are needed by nearly every later feature. The roadmap had grown into a long checklist with no clear reason for its order. A checklist in a Markdown file is also a poor way to track day-to-day work.

## Decision
**Build in this order:**
1. **Finish the journal:** correctness first, then the P/L calendar, charts, trading metrics, account values and manual entry. None of it needs market data.
2. **Foundations:** the Settings page, alerts in the app, and backups.
3. **Setup and friend beta:** friends start using the journal.
4. **Live data:** broker market data and sockets, then everything built on prices.
5. **Bots.**
6. **Packaging.**

**Track the work in a GitHub project:** one issue per piece of work, one milestone per phase, with Phase, Size and Status fields. roadmap.md stays as the short summary and links to the project.

## Why
- **Shared foundations first,** so that each later feature is built once on top of them. For example, settings added before the Settings page exists would each have to move out of `.env` later.
- **Correct numbers before charts:** every chart and metric reads the journal's P/L.
- **Friend beta before bots:** real feedback arrives while the rest is built, and Schwab's days-long developer app approval runs alongside our work. The cost is that friends see only the journal until the bots arrive.
- **Bots last:** they're the riskiest part, so they're built on proven data, alerts and settings. They can still be developed in preview mode under gateway mode, because the trading guard allows previews.
- **Issues over checklists:** issues link to the commits that close them, show progress per milestone, and hold the discussion for each piece.

## Consequences
- **The roadmap no longer has checkboxes.** Progress is tracked in the project, so the two can't disagree.
- **Live P/L moves** from the journal phase to the live data phase. The journal phase only marks expired trades as waiting on the broker; pricing them from the broker's close comes with live data.
- **Issues follow the same rules as the docs:** no account numbers, credentials or personal P/L figures.
