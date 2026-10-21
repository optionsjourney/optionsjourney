# 0028. One order path for everything; two pause switches replace "Stop all bots"

- **Status:** Accepted
- **Date:** 2026-10-11
- **Amends:** [0009](0009-bot-safety-defaults.md) (its "Stop all bots" switch)

## Context
Bots, managed trades' exits and buttons like Close all send orders. 0009 asked for one central check and a "Stop all bots" switch that stops all new orders. As built, that switch refused every order, exits included, so hitting it in a panic would also switch off every stop. Tracker2's kill switch blocks entries only. "Stop all bots" is also the wrong name once orders don't only come from bots.

## Decision
- **One order path:** a broker-neutral order and one central check, in a pure `trading/` package, and one service that is the only code that sends, cancels or replaces an order. It serves both brokers and every origin (bot, managed trade, you).
- **Closing orders can only make a trade smaller.** They are checked against the open position and against working closing orders already at the broker. Only bots open trades in Phase 6.
- **Two switches:**
  - **No new trades:** opening orders are refused. Exits, stops and closes keep working.
  - **Freeze all orders:** the app sends nothing, not even cancels, and stops watching open trades. Orders already at the broker stay, and can still fill. The user is told exactly this before freezing, and a banner says it while frozen.
- If a switch can't be read, the app acts as if Freeze is on.

## Why
- One path means none of the protections can be skipped, which is what 0009 asked for.
- Most of the time the user wants to stop new risk, not drop protection. Freeze is for when the app itself can't be trusted.
- The words are the app's own, chosen with the author. They differ from OptionOmega's "Pause All Trading" and "Pause Opening New Trades" on purpose.
- Turned down: **one switch that blocks everything** (leaves trades unprotected), **one switch that blocks entries only** (no emergency stop), and **a separate path for exits** (two places to get the safety rules right).

## Consequences
- The existing `_bots_stopped` setting becomes Freeze, so nobody ends up less stopped than before.
- An exit can be refused when the broker's working orders can't be fetched. That's safer than possibly over-closing, but it can fail during a broker outage, so it raises an alert.
