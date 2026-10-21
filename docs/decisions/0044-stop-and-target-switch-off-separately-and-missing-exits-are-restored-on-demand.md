# 0044. The stop and the target switch off separately, and missing exits are restored on demand

- **Status:** Proposed (built under the author's standing approval for the parity roadmap; question 4 ruled by the author on 2026-10-14; waits on review)
- **Date:** 2026-10-13
- **Replaces:** slice 6's single "exits off" switch per side (bots slice 6 spec, question 5). [0027](0027-bot-engine-and-managed-trades.md) and [0028](0028-one-order-path-and-two-pause-switches.md) stand.
- **Builds on:** [0043](0043-futures-bots-set-a-profit-target-only.md) (futures: a profit target only)

## Context
Slice 6 gave each side of a bot trade one switch, "Turn exits off": it cancelled the stop, disarmed the target, and stopped the timed close and time actions. The issue asked for one switch. Tracker2 has two (`BotPositionOverride.stop_loss_off`, `profit_target_off`), and issue #101 asks for them.

Tracker2 also restores a trade's missing resting exits on demand, with a dry run (`rearm.rearm_trade`, `rearm_all`). It was built after a target died at the broker and nothing put it back. This app re-arms a stop by itself up to 3 times a side a day, then leaves the side to you.

## Decision
- **Two switches per side:** Stop off and Target off. "Turn all exits off" stays as both at once.
- **Existing trades keep their meaning:** a trade with exits off has both switched off (migration 0030).
- **Exits off means "no stop or target", not "hands off"** (the author's ruling, 2026-10-14, following Tracker2). This replaces slice 6's promise (its spec, question 5) that a side with exits off is skipped by the timed close and time actions. With one switch off or both:
  - the timed close still closes the side (Tracker2's `timed_close.fire_timed_closes` reads only its own `timed_close_overrides`). The run page says so in the exits-off box, and an alert goes out as it closes;
  - watch rules still close it (Tracker2's `watch_rules` and `watch_loop` never read `stop_loss_off`/`profit_target_off`);
  - profit actions still run (`exits.exits_config` drops only the stop and target levels, never the actions), and a profit action's stop move turns a switched-off stop back on (`management.record_profit_action_fill`, reached from `scale_out`: "the action sets a stop, even over a removed one"). That move is checked against the market first; if the stop would fire at once, it stays off;
  - a time action still runs (the author's follow-up ruling): its close closes, and its stop or target move switches that exit back on at the action's level (Tracker2's `management.write_time_action_override`: `stop_loss_off` / `profit_target_off` set False). A stop is checked against the market first and stays off if it would fire at once, as for a profit action.
- **Restore missing exits** is on each run page and, for every open trade, on the Automation page:
  - **Check** lists what's missing and sends nothing.
  - **Restore** places only what's missing, through the paths every exit takes. A stop goes back at its last level, after the live-price check, sized to what's held. It never goes over a stop the app can't show is gone at the broker.

## Why
- It's how Tracker2 does it, and a stop you want off is not a target you want off.
- Restore makes "the side has no stop" something you can fix with one button, with its dry run, instead of setting each level again by hand.
- Turned down:
  - **restoring without the live check** (Tracker2 checks only a roll's follow-up): a stop that would fire at once must never be placed.

## Consequences
- **A behaviour change for trades with exits off today:** their timed close, watch rules, profit actions and time actions now run, and a profit action's or time action's move switches the stop or target back on. Slice 6's ruling that exits off stops them is replaced here; decision 0027 and the slice 6 spec aren't rewritten.
- The integrity check reports a side with its stop off ("you switched its stop off"). A side with only its target off isn't a finding: it still has its stop.
- Migration 0030 replaces `exits_off` with `stop_off` and `target_off`; downgrading keeps `exits_off` only where both were off.
- Not built yet: per-trade timed close, watch rules, profit actions, stop style and wing rule; choosing which exit rests at the broker; closing another group after a stop; the futures switches and adding a stop by hand to a futures trade.
