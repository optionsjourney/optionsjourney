# 0043. Futures-options bots set a profit target only, and enter only from the Trade page

- **Status:** Accepted (the author's rulings, 2026-10-13)
- **Date:** 2026-10-13
- **Replaces, for futures options only:** [0036](0036-new-entry-styles-only-where-the-exits-can-protect-them.md)'s rule that every entry needs a working stop. 0036 stands for every other bot.
- **Builds on:** [0028](0028-one-order-path-and-two-pause-switches.md) (one order path), [0041](0041-the-trade-page-can-send-a-bots-entry.md) (Manage with a bot), [0042](0042-bots-trade-xsp-and-the-big-index-etfs.md) (free-text underlyings)

## Context
Every bot so far protects its trades with a stop that really works (0036, 0039). Tracker2 runs its futures-options bots (/ES, /MES, /GC) differently:
- they have a **profit target only**, and no stop ("A futures run places PT only, whatever stop the strategy has configured", spec `2026-10-03-analyzer-futures-bot-trade-design.md`);
- the entry is a **resting limit** at your price, sent at any hour, never chased (`analyzer_entry.run_analyzer_entry`: "Futures trade as a Limit only");
- it trades through **TastyTrade only** (`FUTURES_TT_ONLY`).

Issue #100 needed a decision first, because that clashes with 0036.

## Decision
- **A futures bot sets a profit target and no stop** (the author's ruling). Each group's target rests at TastyTrade as a GTC closing limit. A stop is refused when the bot is saved, and every group must have a target, since it's the trade's only exit.
- **It enters only from the Trade page's Manage with a bot** (the author's ruling): never on a schedule, never armed, no Preview now.
- **The entry is your reviewed limit, resting at TastyTrade** until it fills, you cancel it, or the broker ends it, as in Tracker2. A partial fill gets Tracker2's 60 seconds in the regular session; then the filled part gets its target and the rest is placed again as its own run.
- **TastyTrade only.** Schwab takes no futures orders.
- **The pages say plainly "Futures: profit target only, no stop"**: the bot page, the Trade page's picker, the run page and the journal's chips.
- The other exits (timed close, time actions, watch rules, profit actions, the closing order) and the entry rules (window, market filters, default alerts) aren't built for futures, and a futures bot refuses them.
- Every order still goes through the one order path: preview vs live, the size limits, Freeze, No new trades and the trading guard.

## Why
- It's how Tracker2 trades futures options, and what the author asked for. A futures option rarely gets a resting single-leg stop worth having, and it trades through the night.
- A resting target works while the app is closed and overnight. That matters more for a futures trade than for a same-day SPX one.
- Refusing a stop at save, rather than ignoring it as Tracker2 does, keeps the bot's page honest about what it does.
- Turned down:
  - **a stop the app watches** (as for Schwab bots, 0037): it needs the app running at every hour on futures quotes, and Tracker2 doesn't do it;
  - **chasing a futures entry**: Tracker2 refuses it.

## Consequences
- A futures trade can lose more than any other bot trade: nothing closes it on a loss. The pages say so where you choose it, and the 09:20 check confirms its target is still resting.
- If you cancel or change the target at TastyTrade, the app alerts at once and marks the run Needs you. It doesn't put the target back.
- **Not built yet** (the spec's Out list):
  - adding a stop by hand, changing the target, turning exits off or closing part from the run page;
  - Modify on a resting entry, and following an entry replaced at the broker;
  - an expiry warning before a series' own expiry time.
- Migration 0029 adds a run's multiplier, contract, profit-target-only flag and entry mode, with defaults that leave every existing run as it was.
