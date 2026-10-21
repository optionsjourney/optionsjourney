# 0048. Trades take either side, and each group chooses which exit rests at the broker

- **Status:** Accepted (approved by the author as a spec on 2026-10-14; built on branch `bots-either-side`, waiting on review and a preview check on a real account)
- **Date:** 2026-10-14
- **Builds on:** [0028](0028-one-order-path-and-two-pause-switches.md) (one order path), [0037](0037-schwab-bots-stops-are-watched-by-the-app.md) (Schwab: stops watched), [0039](0039-any-same-day-structure-with-a-stop-that-always-works.md) (watched groups), [0043](0043-futures-bots-set-a-profit-target-only.md) (futures: a profit target only), [0047](0047-per-trade-settings-and-closing-another-group-after-a-stop.md) (pairing)

## Context
A bot said **Opens for: Credit / Debit**, and an entry that priced the other way was skipped. Some trades (broken-wing butterflies, risk reversals, uneven iron flies, anything picked by delta) land on either side from day to day. TastyTrade takes only one closing order per contract, so a group can rest its stop or its target, not both. Tracker2 keeps `premium_side` for its premium bounds and lets `broker_exit` matter only for a group that is one sold option.

## Decision
- **No Opens for.** A trade takes the side its midpoint prices on; under $0.05 either way it's skipped as too close to even. Premium bounds compare the price's size, either side.
- **Each trade is managed by the side it filled on.** On a credit, % targets are the credit kept (100% or more: no target, alerted). On a debit, % of the credit is the same loss less 100, stops on the short's fill or off the credit don't translate (no stop, alerted), and the closing order is ignored (alerted). A debit target is 1 to 1000%, a credit target 1 to 99%.
- **Each group chooses Rests at the broker: Stop or Profit target**, on TastyTrade only. A credit fill with one sold leg rests its stop unless the group chose its target; every other group rests its target as one closing limit for all its legs (good till cancelled, so it stays on overnight) and the app watches its stop (on the short's quote, or on the group's P&L). A debit fill on a group that chose Stop falls back to that and says so when the trade is entered.
- **Every close first pulls the resting target and reads the cancel back.** The watched stop's close, a timed close, a close you ask for, and a profit-action or watch-rule close all go through the same handshake. A failed cancel holds and alerts; a target found filled is recorded and nothing else is sent; a close that didn't close places the target again. A resting target's fill ends the watch. When the app can't tell whether the target still rests (`TARGET_UNCLEAR`), it holds that side, tells you, and the group's stop isn't watched until you check. A resting target's send that got no clear answer, or that a crash cut off while it was still "placing", is held the same way. A close that didn't close puts the target back before the stop, and never while another close runs on the group.
- **The stop the app holds on a short is marked on its row** (`bot_exits.app_held`), because the run's broker alone can't say it: a TastyTrade row whose target rests has a stop the app watches, and a close that gives up must not send it as a second closing order.
- **The arming box counts what the app might watch.** `watched_groups` counts any group with a stop that the app watches, and any group set to rest its target, with or without a target number (on a debit fill its stop is watched either way). A group set to Stop that might fall back on a debit fill is not counted; the page and the guide say so.
- Migration 0033: a debit bot's groups get Profit target, all others Stop; open trades keep what they started with. Bot file version 3 drops `opens_for` and adds each side's `rests`; versions 1 and 2 still import.

## Why
- People don't think of a bot as credit or debit, and a trade that lands on the other side shouldn't be lost.
- One place (`bots/exit_plan.py`) decides, for a group and a fill, what rests and what's watched, so the entry, the Trade page and the preview agree.
- Cancel and read back before any close keeps one closing order per group (0028), the way a reached target already pulls a resting stop. Doing it for every close, not only the watched stop's, is what keeps a timed close from sending a second one.
- Turned down: keeping the wrong-side skip as an option (premium bounds do that job and the guide recommends them); a resting target on Schwab (unchecked against a live account); counting every bot whose legs could price as a debit in the arming box (more ticks for little gain).

## Consequences
- Existing debit bots change on their next trade: the target rests at TastyTrade instead of being watched.
- A credit spread's quotes that go bad can now enter as a debit within its premium bounds: the guide recommends bounds on every bot. A single sold leg that prices through zero inside a credit trade is still refused as inverted.
- A group that falls back on a debit has its stop watched without the arming box having been ticked for it, if it was set to Stop; the entry alert says so.
- Changing the choice on an open trade, a resting target on Schwab and a stop-sell on one bought option are later work. A time action that sets a target on a resting group whose target you switched off places it again at TastyTrade (`_resting_back_on`).
- New groups start on **% above the short's fill**, as before, not on "% of entry price lost" as the spec first said: on a group with one sold leg (a credit spread) that style is measured on the short with the wing counted as worthless, so it fires early or is refused (the controller's ruling on the final review). It stays available, and the bot page and guide explain it.
