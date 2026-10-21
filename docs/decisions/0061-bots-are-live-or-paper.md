# 0061. Bots are Live or Paper; Preview becomes a button

- **Status:** Accepted
- **Date:** 2026-10-16
- **Issue:** #146
- **Design:** Paper trading
- **Changes:** the "new bots start in preview" part of [0059](0059-schwab-bots-need-no-switch.md). Its other safety steps stand.

## Context
A bot today is Preview or Live. A Preview run checks the order with the broker and keeps nothing more, so a friend can't see what a strategy would have done or follow a trade through the app before risking money. Tracker2's paper mode is the same as our Preview and tracks nothing after entry.

## Decision
- A bot has two modes, **Live** and **Paper**. A Paper run follows all the bot's rules, pretends to fill at mid, has every exit watched by the app, and records an estimated result in a separate Paper journal. Paper results never mix with real ones.
- Every paper screen and alert says plainly that fills are estimated at mid and stops are watched by the app, so real results would differ.
- New bots start **Live and disarmed**. Arming a Live bot says plainly that it sends real orders. Choosing Paper is the friend's own switch.
- Bots in Preview mode today become **Paper**, never Live.
- Preview stops being a mode. A **Preview order** button checks the order with the broker and shows a risk graph, without saving a run.
- Switching a bot to Live ends its paper trading: open paper trades close at mid and are no longer watched.
- Paper orders still pass the one central check as previews, so size limits, Freeze and Stop all bots apply. No paper path can send a real order.

## Why
- Paper trading lets a friend learn the app and test a strategy without money at stake.
- A separate journal keeps real P/L honest.
- Making Paper a deliberate choice keeps the bot's mode clear, while disarmed-by-default still means nothing trades until the friend arms it.

## Consequences
- Arming a new bot now means real orders, so the arm step must say "Live: sends real orders".
- Paper results depend on the app running during market hours to watch exits; gaps are reported like watched-stop lapses.
- This goes beyond Tracker2 parity.
