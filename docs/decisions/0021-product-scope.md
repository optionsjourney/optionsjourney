# 0021. What the app includes, and what it leaves out

- **Status:** Accepted
- **Date:** 2026-10-10

## Context
[0001](0001-fork-tracker2-into-a-simpler-app.md) chose which parts of Tracker2 to bring over, before any code existed. Once the journal was built, the author set out the full picture of the finished app. Some of it differs from 0001: the analyzer and alerts were on 0001's "leave out" list, and they're now in. Some of it also changes how strategies and bots relate ([0004](0004-automation-first-strategy-naming.md)) and where settings live.

## Decision

**Features the app will have:**
- **Journal:** every trade, sliced by the user's own strategies, tags and dashboards ([0019](0019-journal-strategies-tags-dashboards.md)).
- **Bots:** downloaded from a shared library, starting in preview mode ([0005](0005-shareable-bots-as-json.md), [0009](0009-bot-safety-defaults.md)). This covers everything the bots need from Tracker2's bot engine, including exit protections and re-arming them, and the reconciler that matches bot orders to fills.
- **Analyze:** Tracker2's analyzer.
- **Charts:** Tracker2's chart page (`/chart`), the grid of market price charts.
- **P/L calendar.**
- **Trading metrics:** Tracker2's trading metrics.
- **Account values:** each account's value over time.
- **Live P/L and open positions.**
- **Manual trade entry** ([0003](0003-brokers-and-trade-input.md)). What it covers still needs defining.
- **Alerts:** alerts shown in the app are the base. Discord and Telegram are optional add-ons the user can switch on.
- **Database backups:** an optional regular backup. The user chooses how often and where backups go.

**Left out:**
- Tracker2's strategy metrics page.
- Tracker2's other tools: morning brief, MEIC backtest, market report, IV monitor, wheel scanner, and Tracker2's own dashboard page. Dashboards here are the journal's saved views, not that page.

**Foundations:**
- **No fixed journal pages.** Tracker2's 0DTE, Calendars and Options journals aren't brought over. The journal is built on user-defined strategies and tags.
- **A strategy is the parent of its bots.** One strategy can have many bots, and every bot belongs to exactly one strategy. A bot's trades are filed under its strategy automatically.
- **Stocks stay.** Shares are journaled when they show up in the transactions, for example from buying shares, selling them, or an option assignment. The app doesn't read the shares currently held in an account.
- **Settings live in the app.** Configuration is done on a Settings page, not in `.env`. The only things left in `.env` are gateway mode (a development tool, [0006](0006-dev-gateway-credentials.md)) and the few settings needed before the database can open: where the data folder is and which port to use.
- **Live data comes from whichever brokers the user has.** Live data means fills, quotes and chart data, streamed over the brokers' sockets.
  - **Schwab only:** everything comes from Schwab's streamer.
  - **TastyTrade only:** everything comes from TastyTrade's streamer.
  - **Both:** TastyTrade is the main source, and Schwab is the backup, as in Tracker2.

## Why
- **Analyze, charts, metrics and alerts are what the author uses every day** alongside the journal. Without them the app is only a log.
- **The left-out tools fit one person's trading** or are research projects. Each needs its own data or upkeep, and friends wouldn't use them.
- **Strategy as parent fits automation-first naming.** A bot already knows its strategy ([0004](0004-automation-first-strategy-naming.md)). Several bots (different times, sizes or accounts) running one strategy roll up into one strategy P/L.
- **Tracker2's `.env` grew out of hand.** Friends who aren't programmers can't edit a hidden text file, and a settings page can explain and check each value.
- **Following the user's brokers means no extra accounts,** such as a separate market data service. Using TastyTrade first when both are connected follows what proved reliable in Tracker2.

## Consequences
- **This replaces part of [0001](0001-fork-tracker2-into-a-simpler-app.md):** the analyzer moves from "leave out" to "bring over". Discord and Telegram come back, but only as optional add-ons. 0001's other choices stand.
- **This amends [0004](0004-automation-first-strategy-naming.md):** a bot no longer just stamps its own name on its trades. Its trades get its parent strategy, and the bot is recorded too so that the journal can filter by bot.
- **Settings move into the database.** Each new setting goes on the Settings page from now on. The existing `.env` settings move once the Settings page exists, and broker logins go to the system keychain (Phase 4).
- **A streaming layer is needed** before live P/L, charts, Analyze, alerts and bots can work. It picks the source per broker setup and fails over from TastyTrade to Schwab.
- **Shares bought before the sync window** have no opening transaction, so selling them shows up as a close with no matching open. Reading current holdings could fix that later.
- **Still to decide:**
  - **Economic events.** Tracker2's bots use them to skip days like FOMC and CPI. That needs a source for the calendar, or the bots drop that check.
  - **Manual trade entry:** what a user can enter, and how it sits next to synced trades.
  - **Where backups go:** which choices the app offers.
