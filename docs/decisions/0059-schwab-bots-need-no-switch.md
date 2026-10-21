# 0059. Schwab bots need no switch

- **Status:** Accepted (its "same-day only" for Schwab bots is lifted by [0060](0060-schwab-bots-rest-their-exits-at-schwab.md); "new bots start in preview" is changed by [0061](0061-bots-are-live-or-paper.md))
- **Date:** 2026-10-16
- **Replaces:** the switch part of [0037](0037-schwab-bots-stops-are-watched-by-the-app.md) (`SCHWAB_TRADING_ENABLED`). The rest of 0037 stands: a Schwab bot's stops are watched by the app, with the same warnings, tick box and alerts.

## Context
Decision 0037 put Schwab bots behind `SCHWAB_TRADING_ENABLED` in `.env`, off by default, until the author had watched one through a live day. TastyTrade bots haven't been through a live day either, and since decision 0039 many TastyTrade groups have app-watched stops too, so the two brokers carry the same risk. The switch meant a friend with only a Schwab account couldn't use bots at all, unless they edited `.env`, which friends shouldn't be asked to do.

## Decision
- Bots trade through TastyTrade or Schwab alike. The form, import and clone offer every synced TastyTrade and Schwab account, and entries go through either broker. `SCHWAB_TRADING_ENABLED` is gone.
- Everything that protects a Schwab bot stays: new bots start in preview and disarmed, the yellow box on its page, the tick box to arm it, go live or Preview now while live, every alert from 0037, and the one order check with its preview, size limits and Stop all bots.
- Schwab bots still trade same-day only, and futures bots still trade through TastyTrade only.

## Why
- The same safety steps apply to both brokers, so one broker shouldn't be held back behind a hidden setting while the other isn't.
- Preview mode, the tick box and the trading guard in gateway mode already stop a bot sending real orders by accident.

## Consequences
- A Schwab-only user can make and run bots. Stock and ETF bots still need TastyTrade connected for their price steps, so with only Schwab connected, only SPX and XSP bots can trade.
- An existing `SCHWAB_TRADING_ENABLED` line in a `.env` is ignored.
