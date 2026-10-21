# 0037. A Schwab bot's stops are watched by the app, behind a switch

- **Status:** Proposed (the switch is replaced by [0059](0059-schwab-bots-need-no-switch.md); the watched stops by [0060](0060-schwab-bots-rest-their-exits-at-schwab.md))
- **Date:** 2026-10-13
- **Builds on:** [0027](0027-bot-engine-and-managed-trades.md) (the bot engine), [0028](0028-one-order-path-and-two-pause-switches.md) (one order path), [0030](0030-bots-built-as-thin-slices-in-tracker2s-tables.md) (bots follow Tracker2), [0009](0009-bot-safety-defaults.md) (bot safety defaults)

## Context
Until now bots traded through TastyTrade only, and every stop rested at TastyTrade as a single-leg stop-market order: it protects the trade whether or not this app is running. Bots slice 11 (#87) brings bots to Schwab. Tracker2 rests the same single-leg stop at Schwab, but this app has never checked that Schwab accepts one on SPXW, and the issue asks for software-driven exits. Tracker2's own design keeps "the software exit" (its engine-watched stop) for exactly that case.

## Decision
- A Schwab bot's stops are **watched by the app**: the app reads the short's price every 2 seconds in the regular session and, when its mid is at or past the stop on two checks in a row from fresh prices, closes the side: the exit chase, then **always** the natural price and the leg-out, ending with the short bought at market, whatever the bot's own ending for other closes (the controller's ruling on the final review: a watched stop must actually close). Putting a stop back after its close couldn't run never counts as a re-arm. Nothing rests at Schwab.
- The app says so plainly before a Schwab bot can be armed, made live or previewed while live (a box to tick), and on its page (a bot moved to another broker is disarmed and put in preview): the stops work only while the computer is on and awake and the app is running and connected.
- It **fails safe and loud**: a stale price never closes anything and alerts after a minute; a gap in watching alerts with its times, at startup too; a reached stop under Freeze sends nothing and alerts; a stop that has fired three times in a day without closing the side is left to you with an alert.
- Schwab bots are off behind `SCHWAB_TRADING_ENABLED` (Tracker2's switch), off by default, until the author has watched one through a live day. TastyTrade bots are unchanged.
- Refresh-login reminders go out as safety alerts 48 and 12 hours before the 7-day Schwab login runs out, and when it has.

## Why
- An unverified resting order is worse than a watched one: a refused stop leaves the side with no stop, found only when it's needed.
- Tracker2's engine-watched stop is already the fallback in its Schwab design, with two confirmed passes and fresh-data rules that have run on real trades.
- A friend's laptop sleeps. A stop that silently stops working is the worst outcome, so every way it can stop working is said out loud.
- Turned down: **resting the stop at Schwab now** (unverified); **a market order on the short when reached** (Tracker2 closes the side with its close job instead); **re-firing without limit** (a close that keeps failing would send orders all day).

## Consequences
- A Schwab trade is only as protected as the computer running the app. The guide says so, and so do the pages.
- Exits now follow the run's broker (stored on the run, migration 0026), not the bot's.
- A later slice can rest the stop at Schwab once a live check shows Schwab takes it; this decision would then be replaced.
