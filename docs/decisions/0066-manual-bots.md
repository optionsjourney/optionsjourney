# 0066. Manual bots: reusable exit rules for a trade you opened

- **Status:** Accepted
- **Date:** 2026-10-17

## Context
Tracker2 has Analyzer bots: a bot with no ticker or schedule that you attach to a trade you chose yourself. This app had nothing like it, so those bots could not be imported (#158, spec `docs/superpowers/specs/2026-10-17-manual-bots-and-chase-endings-design.md`, Part 3).

## Decision
- **A manual bot is a template, not a `Bot`.** Its rules (profit target, stop loss, exit time, default alerts) live in their own table, `manual_bots`. It has no ticker, account, schedule or legs, so it could not be a `Bot` without making those fields optional everywhere.
- **Attaching copies the rules to one trade.** The app makes a hidden per-trade `Bot` (`per_trade`, with `manual_bot_id` pointing back) for that trade and hands the trade over through the same adopt path a bot hand-over uses. Exits, stops, alerts, Freeze and the central order check therefore work as for any bot. Hidden bots never show in the bot list or the ticket's bot picker.
- **Snapshot.** The trade gets the rules as they were when attached (for a pending ticket, as they were at Send). Editing the manual bot later does not change trades already managed; **Change rules** on a trade re-attaches with the new ones.
- **Live, but gated.** A per-trade bot is Live, but every order still goes through `orders.submit`: preview vs live, size limits, Freeze, and the gateway trading guard.

## Rulings (product decisions made while building)
- **Limits kept.** A per-trade bot keeps the normal bot validation (100 contracts, the leg limit, no reverse calendars, refused underlyings). Size limits are a safety rule, and a trade that can't be exited safely shouldn't be managed. Cost: some trades can't be managed until the limits are relaxed.
- **Re-attach after removal.** Removing the rules (**Handed back**) frees the trade, so rules can be attached again. Only a handed-back run frees a trade; a run that ended another way (for example failed) still blocks it, so a trade is never managed twice. Cost: leftovers of a failed scheduled-bot run can't take manual rules.
- **Grow once on partial fills.** When a ticket's order fills only in part, the rules attach to the filled part at once, then grow to the full size once, when the order is no longer working, not on every fill. Cost: a brief gap where units filled after the first are unmanaged until the order finishes. The guide says so.
- **Place only means no rules at all.** An imported Analyzer bot with no profit target, stop, scheduled close and no default alerts in use is "Place only" and isn't imported as a manual bot. One with only alerts imports as an alerts-only manual bot (or Place only with a note that its alerts weren't kept, if manual rules can't hold them).
- **Other rules from the plan author:** a trade with more than one underlying is refused; removing the rules cancels their resting exits; futures keep only the profit target (the stop in either unit, the exit time and the alerts are dropped, and the app says so); a hint appears if any leg settles in shares (assignment).

## Why
- **Same exits, one path.** Reusing the adopt path means no second exit engine to keep safe.
- **Simple for friends.** One list of named rules, picked on the ticket or from the Journal.

## Consequences
- Tracker2 Analyzer bots import as manual bots; legs, chase settings, account, size and ticker are not kept.
- New run status `handed` and a per-trade bot kind hidden from the lists.
