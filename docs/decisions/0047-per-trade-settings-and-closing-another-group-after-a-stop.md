# 0047. A trade can carry its own settings, a stop can close another group, and a futures trade's target and entry are changed in place

- **Status:** Proposed (built under the author's standing approval for the parity roadmap; waits on review)
- **Date:** 2026-10-14
- **Builds on:** [0028](0028-one-order-path-and-two-pause-switches.md) (one order path), [0043](0043-futures-bots-set-a-profit-target-only.md) (futures: a profit target only), [0044](0044-stop-and-target-switch-off-separately-and-missing-exits-are-restored-on-demand.md) (the two switches), [0046](0046-pages-show-what-bots-do-and-only-read.md) (the account stream waits)

## Context
Tracker2's journal drawer gives one live trade its own timed close (`BotRunTimedCloseOverride`), watch rules (`BotRunWatchOverride`) and, per group, its own stop style, closing order, wing rule and profit actions (`BotPositionOverride`); every pass reads the trade's copy before the bot's (`timed_close.effective_settings`, `watch_loop.config_for`, `exits.effective_on_stop_remainder`, `effective_co_mode`). A group can close another group when its stop fills (`on_stop_close_group_id`, `remainder._handle_paired_close`). A futures trade's resting target can be re-priced or removed, and its resting entry modified or followed when it's replaced at the broker (`analyzer_entry.modify_entry`, `_follow_successor`). Slice 6 gave this app a per-trade override for the stop %, the target % and (slice 18) the two switches only.

## Decision
- **A trade's own settings** live beside its bot's: a group's own stop unit and $ stop, closing order, wing rule and profit actions on `bot_trade_overrides`; the trade's own timed close and watch rules in their own tables (a row there replaces the bot's setting wholesale). A missing value or row is the bot's, so nothing changes for a trade nobody changed. **One rule** (`bots/overrides.py`, `watch.rules_for`) says what a held trade's group reads, and every pass goes through it.
- **A save that would act at once asks first:** a timed close already due today, a watch rule already crossed or one that can't be checked on the stream now. A stop style change goes through the same live check and move as Change stop (refused before anything is cancelled).
- **The latest change wins:** a later time action, profit action or % change moves the stop by its own rule and clears the trade's own stop style.
- **Closing another group after a stop** fires only after a stop resting at TastyTrade fills. The paired row is committed with the fill; the other group is then closed by slice 6's claimed close, the way its own wing rule says. One that can't start (out of session, held back, busy) is alerted and never retried; only a crash before the claim is picked up again.
- **A futures target is changed as Tracker2 does: cancelled, the cancel confirmed, then placed again** sized to what's held, and only when nothing of it has filled; an unclear cancel holds the group back. Target off is confirmed by the broker before it counts. A resting entry is modified by one replace, and followed by TastyTrade's replacing order id only when the new order is the entry's own legs and size.

## Why
- Tracker2's drawer is the parity target, and its "replace wholesale" rule keeps each setting's meaning simple.
- Putting what a trade reads in one place keeps the passes from drifting apart.
- Cancel-then-place never rests two closing orders on a futures contract, and never re-prices a part-filled order.
- Turned down: retrying a paired close from the monitor (Tracker2 reports and never retries: the group's state is by then not understood); following an entry by searching orders (this app's orders carry a random tag, not the run's key).

## Consequences
- Migration 0032; every default is null or no row, so every existing bot and trade behaves as before.
- Not built (later): adding a stop by hand to a futures trade, choosing which exit rests at the broker (`broker_exit`; the futures stop would build on it), and fill notices from the account stream (0046).
- Changing a futures target leaves it without a target for a moment; a new one that isn't placed is alerted at once.
