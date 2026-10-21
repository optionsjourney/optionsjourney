# 0060. Schwab bots rest their exits at Schwab, as TastyTrade bots do

- **Status:** Accepted
- **Date:** 2026-10-16
- **Replaces:** the watched-stops part of [0037](0037-schwab-bots-stops-are-watched-by-the-app.md) (its switch was already replaced by [0059](0059-schwab-bots-need-no-switch.md)), and 0059's "Schwab bots still trade same-day only". 0037's alerts for a stop the app watches stand, for every such stop.

## Context
Decision 0037 made every Schwab bot stop watched by the app, with nothing resting at Schwab, because this app had never checked that Schwab takes a resting single-leg SPXW stop. The author has since confirmed it in Tracker2: its exits have no broker special case, it sends Schwab a one-leg stop order and never a multi-leg one, and Schwab shows a waiting stop as `AWAITING_STOP_CONDITION`. Watching every Schwab stop meant a Schwab trade was only as safe as the computer running the app, and Schwab bots were held to same-day trades for that reason.

## Decision
- A Schwab bot's exits follow exactly the rules a TastyTrade bot's do (decisions 0039, 0048, 0054): a credit group of one sold leg that chose Stop rests a one-leg stop market order on its short at Schwab; a group that rests its profit target rests it at Schwab as one closing limit; every other stop is watched by the app, as on TastyTrade.
- The "watched by this app" warning and the tick box to arm, go live or Preview now appear only when the bot has a stop the app watches, as for TastyTrade.
- Schwab bots may hold trades overnight and turn Exact DTE off: the same-day rule was there only because every Schwab stop was watched. Close-another-group-after-the-stop works on Schwab as on TastyTrade.
- **Trades already open stay watched until they close.** Whether the app holds a stop is read from the stop's own record, never from the broker. A migration marks every Schwab stop written before this as held by the app, so it is never left unprotected and never gets a second stop at Schwab.
- Every exit still goes through the one order path and its check (preview or live, size limits, Freeze, Stop all bots). A stop or target is moved by cancelling it, reading the cancel back and placing a new one, never replaced in place, at either broker.

## Why
- Parity with Tracker2 and with TastyTrade: one set of rules is easier to explain and to test.
- A resting stop works with the computer off; a watched one doesn't. Most single-short Schwab stops are now safer.
- Reading "held by the app" from the record keeps open trades exactly as they were, with no special case left in the code for the broker.
- Turned down: **keeping Schwab watched until a live check** (the author has confirmed it in Tracker2); **re-placing open trades' stops at Schwab** (a trade mid-flight would briefly have two stops or none).

## Consequences
- A Schwab trade with a resting stop is protected while the app is off. Groups whose stop is watched still need the app running, as on TastyTrade.
- The author should preview and place one tiny resting SPXW stop at Schwab before trusting a live Schwab bot (the spec lists the steps).
- Messages name the run's broker where they used to say TastyTrade.
