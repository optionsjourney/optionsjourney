# 0062. Settle expiring trades at the close, without waiting for the broker

- **Status:** Accepted
- **Date:** 2026-10-16

## Context
Brokers post an option's expiration record a day or more after it expires. Until then a 0DTE or expiring trade stays open in the journal (shown "Expired, waiting on broker" with an estimate that isn't counted, #23), so the day's P/L, the calendar and the metrics are wrong until the next sync after the broker catches up. Tracker2 settles these itself with a 4:15 PM auto-expire job (`services/expiry_service.py`, `scheduler_service._execute_auto_expire`). Issue #147.

## Decision
- An optional daily job (switch **Settle expirations at 4:15 PM** on the journal page, **off by default**) runs 15 minutes after each trading day's close (16:15 New York, 13:15 on half days). It settles the option legs of open trades that expire that day at the underlying's close: a put's strike less the close, a call's close less the strike, else 0; a short pays it, a long receives it; only the quantity still open. Like Tracker2, TastyTrade's flat $5 exercise fee is added once to a trade with an option in the money.
- **Guard (Tracker2's):** nothing is settled until every synced account has finished a trade sync after the close. The app has no timed sync, so the job syncs first; if that fails, or a close isn't out yet, it tries again every five minutes until 8 PM.
- **Skips** (the trade stays open with the existing waiting/estimate display, and the reason is shown on the journal page): monthly AM-settled SPX/NDX/RUT/DJX options, futures options, a stock option that closed in the money (it becomes shares), no close, an unmatched close on one of the trade's contracts, and a trade sharing a contract with a skipped one. A trade is settled whole (all its legs expiring that day) or not at all.
- **Storage:** a new table `expiry_settlements`, one row per account, contract and direction, with the close used, quantity and fee. Settlements aren't written into positions: when positions are built (`services/journal._build`) each becomes a made-up expiration fill, so the trade closes with reason "Expired" and counts in P/L, the calendar and metrics like any closed trade. A broker's own expiration, assignment or exercise of the contract in that account drops the settlement automatically. The table is part of the positions cache's key.
- The journal marks such trades "Settled at close" until the broker's record replaces the settlement.

## Why
- Following Tracker2 settles the same trades the same way, with the same safety guard.
- Folding settlements in as fills keeps one open/close matcher and one P/L calculation (project rule 4), and makes "the broker's record wins" a filter rather than an update: nothing to undo, nothing to reconcile.
- Off by default because it's a guess about a record the broker hasn't sent; the user chooses it.
- Turned down: writing the settlement into a stored position (this app builds positions from transactions on read; there's nowhere to write it), and settling ITM stock options (the share delivery is only known from the broker).

## Consequences
- 0DTE trades close the same day; the day's P/L and calendar are right that evening.
- If the broker's record differs (an unusual settlement), its record replaces the app's when synced, and P/L moves to the broker's number.
- Settlements are never deleted; once the broker's record arrives they're simply ignored.
