# 0035. Safety alerts always reach every channel; bot alerts are sent off the bots' threads

- **Status:** Proposed
- **Date:** 2026-10-13
- **Builds on:** [0027](0027-bot-engine-and-managed-trades.md) (the bot engine), [0030](0030-bots-built-as-thin-slices-in-tracker2s-tables.md) (bots follow Tracker2)

## Context
Bots slice 9 lets each bot choose where its alerts go (Discord only, Telegram only, or both) and sends them in a short layout made for a phone's lock screen. A bot also raises alerts that mean money is at risk: a side with no stop, an order whose outcome is unclear, Freeze holding a stop back, a side held back, a close that failed. A quieter setting must never hide those. Until now every alert was also sent on the bot's own thread, which could wait up to 10 seconds per channel while a trade was being managed.

## Decision
- A bot's channel choice applies only to alerts a call site marks **routine** (news where nothing is left to do: a fill, a profit action done). An alert that isn't marked is never routine.
- Under that, a fixed check (`core/alert_format.is_safety`) reads every alert's words. One that reads as a safety problem goes to every configured channel, even if a call site marked it routine. Tests pin this for each safety family.
- If the chosen channel isn't set up, the alert goes to every channel that is. An alert is never dropped because of a channel choice.
- Bot alerts are put on a queue and sent by one background thread, in order. The queue never drops anything. A bot never waits on Discord or Telegram.
- A failed send is logged by channel and reason only, never with a web address (Telegram's holds the bot token).

## Why
- Muting by mistake is the failure that costs money; sending one routine alert too many costs nothing. So routine is opt-in, and the backstop errs toward sending.
- Tracker2 does the same in spirit: only one routine alert is kept off the phone, by name (`post_alert(..., telegram=False)`), and everything else goes to the phone.
- Turned down: **marking safety alerts instead of routine ones.** A new alert someone forgets to mark would be muted.
- Turned down: **a thread per alert.** Alerts could arrive out of order, and a burst would start many threads.

## Consequences
- A routine alert that happens to use a safety word ("couldn't", "failed") goes everywhere. That's the safe mistake.
- Alerts reach Discord and Telegram a moment later than before, after any alerts queued ahead of them. If the app quits with alerts still queued, those aren't sent (the bot's run page and the app's own alerts still show what happened).
