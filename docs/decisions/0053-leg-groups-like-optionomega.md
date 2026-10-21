# 0053. Leg groups like OptionOmega

- **Status:** Accepted
- **Date:** 2026-10-15

## Context
Bots follow Tracker2 (decision by default). For leg groups the author prefers OptionOmega: you click legs to group them, and each group has every exit of its own (OptionOmega's `exitConditions`: name, legs, profit target, stop, profit actions, time actions, timed exit, tested exits). Here a group has its stop, target, remainder, pairing and profit actions, but the timed close, time actions and watch rules are bot-wide, and watch rules close the whole trade.

## Decision
Leg groups move to OptionOmega's model, in three steps, each with its own spec, plan and build (#120, #121, #122):
1. The form: tick legs and group them; one panel per group. Nothing stored changes.
2. The timed close and time actions are stored and set per group.
3. Watch rules are per group: each reads its group's legs and closes only its group.

Without leg groups the bot is one group, as today, and its panel is the whole trade's.

## Why
- The author asked for it, and a call side and a put side often want different exits.
- The runtime already closes and records the timed close, time actions and profit actions per group; the settings are what's bot-wide.

## Consequences
- Departs from Tracker2 for groups; other parts still follow it.
- Existing bots trade as before: steps 2 and 3 copy bot-wide settings into each group. Bot files get version 4 in step 2; older files are converted on import.
- Run-page changes to the timed close and watch rules become per group (steps 2, 3).
