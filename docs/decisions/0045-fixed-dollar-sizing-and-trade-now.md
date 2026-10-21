# 0045. A debit bot can size its entries by dollars, and a live bot can be traded now

- **Status:** Proposed (built under the author's standing approval for the parity roadmap; waits on review)
- **Date:** 2026-10-14
- **Builds on:** [0028](0028-one-order-path-and-two-pause-switches.md) (one order path), [0036](0036-new-entry-styles-only-where-the-exits-can-protect-them.md) (new entry styles only where the exits can protect them; it left fixed-dollar sizing out), [0039](0039-any-same-day-structure-with-a-stop-that-always-works.md) (debit bots)

## Context
Tracker2 sizes a bot either by a contract count or by a dollar amount (`Bot.allocation_type`, `max_allocation_per_trade`; `sizing.size_entry`). Dollars are divided by the worst debit the entry chase could pay, and only for a debit: a credit's risk is its margin, not its price. Slice 10 left this out (0036) because every bot then opened for a credit. Slice 13 added debit bots.

Tracker2 also has Trade now (`automation_bp.api_run_now`): a live entry off schedule, sent once you type `TRADE`. It obeys only the kill switch and the open market (`guards.check_manual`), and slice 15's Trade page entries add the contract and open-trade limits.

## Decision
- **Dollar sizing** is a choice on a bot that opens for a debit. Each entry buys ⌊ dollars ÷ (worst debit × 100) ⌋, where the worst debit is the price of the entry chase's last try on the order's own ticks. It is capped at Max per trade; an entry that would pass Max per day is skipped, not cut down. A worst price that can't be worked out refuses the entry; dollars that buy nothing skip it with an alert. It sizes the bot's own entries only (scheduled, the entry window, Preview now, Trade now). The Trade page's entries keep the size you reviewed.
- **The dollar setting is private:** the bot file doesn't carry it, as Tracker2's share format doesn't.
- **Trade now** is on a live bot's page. It needs `TRADE` typed exactly (and the "I understand" box for a bot whose stops the app watches). Pressing it is the choice to trade now, so arming, the schedule, blackout days, the market filters and the premium bounds are skipped; Freeze, No new trades, the session, Max per trade, Max per day, Max open trades and the central order check are kept (the controller's ruling, 2026-10-14). It counts as one of the day's entries, and its confirm says so.

## Why
- Dollar sizing is how a debit strategy is usually run (risk a fixed amount per trade). Sizing by the worst reachable price means no fill spends more than the amount.
- Trade now follows Tracker2 and the Trade page's entries (accepted in slice 15): arming and the filters are about when a bot trades on its own, while the limits and switches stay, so it is never a way past the bot's sizes.
- Turned down:
  - **dollar sizing on credit bots** (Tracker2 refuses them too);
  - **shrinking to fit the day's limit** (the contracts path skips, so this one does too);
  - **Trade now with every scheduled check** (this slice's first default).

## Consequences
- Every existing bot keeps sizing by contracts (migration 0031).
- The order path's limits still check every send, whatever the sizing says.
- A Trade now run counts toward Max entries per day; a Preview now run still doesn't.
- Not built yet: sizing by dollars for a credit (by its margin), and a dollar amount per day.
