# 0132. The loss boost: a bigger next entry after a stopped trade

- **Status:** Accepted
- **Date:** 2026-11-14
- **Adds to:** 0050 (sizing by buying power), 0058 (Auto contract limits) and 0061 (Paper bots).

## Context
Tracker2 added an opt-in "boost after a loss" (16dad587, 168d2088): when a bot's previous filled trade hit a stop, its next entry's quantity, or its dollar allocation, is multiplied by a whole number (2 by default). Max contracts per trade still caps it, and an alert says when a boost fires. Its backtest found that entries on the day after a stop earned more on average than entries overall. Issue #328 ports it. Tracker2 settles most of it. Three things it doesn't settle for this app: which trades count for a Paper bot, what "hit a stop" means in this app's exit records, and what a blank (Auto) contract limit does, because this app's Auto isn't Tracker2's.

## Decision
A bot can switch on **Boost after a stop**, with a multiplier from 2 to 10 (blank reads 2). Each entry the bot makes by itself (an entry time, its entry window or Trade now) looks at the bot's **previous trade** and is multiplied when that trade hit a stop:

- **Previous trade:** as Tracker2 defines it. The most recent run that filled (`filled` or `partial`), not a tranche, on an **earlier** New York trading day. Today's own trades never count, so a stop that fills after an entry boosts the next day's entries. A trade sent from the Trade page doesn't count (Tracker2 leaves out its analyzer's). A trade taken over by the bot, or started with Trade now, does.
- **Paper for paper, live for live:** a Paper bot looks at its last paper trade, a Live bot at its last live one. A bot that has just gone Live doesn't take a boost from a paper stop, and a paper trade never sizes a real order. Tracker2 has no separate paper runs to settle this.
- **Hit a stop:** any group of that trade or its tranches was closed by its stop. In this app's records that means:
  - a `bot_exits` row of kind `stop` that is `filled` (a resting stop);
  - a `close` row started by a watched stop (its first log entry's `by` is `stop`) that is `filled` or `reduced`;
  - for a paper trade, a `paper_fills` exit whose reason is `stop` (or `stop (last price)`).
  This covers Tracker2's `stop_loss` exits that are `filled` or `partial_close`. A short-only stop that leaves the wing open counts, as its stop row filled. Targets, timed closes, time actions, watch rules and closes you asked for don't count.
- **What it multiplies:** the contracts asked for (the bot's own, or an entry time's own number), or for a bot sized by buying power, the most buying power per entry (0050).
- **Limits:** a typed **Max contracts per trade** still cuts a boosted entry down, and a typed **Max contracts per day** still skips an entry that would pass it. With the boost on, **Auto** (0058's worked-out limit, the bot's own biggest entry) counts the boosted size: each entry size is multiplied, never past 100 contracts. Without this, Auto would cut every boost back to the usual size, and the central order check would refuse the boosted order. A bot sized by buying power keeps its Auto of 100 contracts.
- **The order check is unchanged:** every boosted order goes through `orders.submit` and its size limits like any other (preview vs live, the limits, Stop all bots).
- **Seeing it:** a routine alert when an entry is boosted ("Paper:" first for a Paper bot), naming the contracts, any cut by Max contracts per trade, and the stopped trade. The run page shows a **Boosted after a stop** step. Preview order shows the boosted size, as Tracker2's preview does; Preview now boosts too but sends no alert. The bot page's summary and a shared file's page show the boost.
- **Sharing:** both settings are in a shared bot file, as part of the bot's design, as in Tracker2's share format, and they import from Tracker2 files under the same names.
- **If the lookup fails,** the entry goes out at its usual size: a missing boost is safer than a missed entry.

## Why
- Following Tracker2's definition keeps the backtested behaviour.
- Paper and live trades are different kinds of evidence. A Paper bot's results should be what the bot would do live, and a pretend stop should never size a real order.
- Auto exists so a blank limit "just works" at the bot's own size (0058). With the boost on, the boosted entry is one of the bot's own sizes, so Auto has to include it. Anyone who wants a hard ceiling types one, and a typed limit is never raised.

## Consequences
- With the boost on and Auto limits, the worked-out ceiling is higher on days with no boost too. The bot still only asks for its usual size then, and a typed limit gives a hard cap. The form's Max contracts per trade ⓘ and its Auto note say so.
- A stop that fills after the last entry of a day boosts the next trading day's entries, not that day's.
- Migration 0058 adds `bots.use_loss_boost` and `bots.loss_boost_multiplier`.
