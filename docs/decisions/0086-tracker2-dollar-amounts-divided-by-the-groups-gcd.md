# 0086. A Tracker2 bot's $ amounts are divided by each group's gcd on import

- **Status:** Accepted
- **Date:** 2026-10-20

## Context
Decision 0084 noted that a $ stop on a ×N group means different things in the two apps. Here a group's unit is its legs' ratios over their gcd (`exit_math.group_legs`, decision 0039), so one unit of a ×3 short is one contract, and the form says "$2.00 a contract". Tracker2 prices a group's $ amounts per the full ratio: `exits.usd_stop_trigger` divides `(E_g − level)` by the leg's quantity, and `group_entry_net` multiplies each fill by it. A $2.00 stop on a ×3 short triggers at the fill + 0.67 in Tracker2, but at the fill + 2.00 once imported here, which is about 3 times the loss. The same factor applies to $ targets, $ profit-action levels and profit-action $ stops, on any group whose ratios share a factor (a 2:2 spread too). Issue #218.

## Decision
- The Tracker2 import (`tracker2_file._dollars_per_unit`) divides each group's $ stop, $ target, $ profit-action levels (when the target is in $) and profit-action $ stops by the group's gcd, to the cent, and adds a note naming the group. A whole-trade Tracker2 bot uses the gcd of all its legs.
- Bots made here, and this app's own bot file, keep "$ a contract" (a $ per unit of the reduced ratio) as before.
- A % amount is the same in both apps and is left alone.

## Why
- An imported bot should risk what it risked in Tracker2. Converting at the one place a Tracker2 amount enters the app does that without changing how any existing bot here behaves.
- Changing the meaning app-wide to Tracker2's would tighten every $ stop on a ×N group already set up here, and would undo 0084's "one unit is one contract".
- To the cent: the trigger is rounded to the option's tick afterwards anyway, so the difference is under half a cent a contract.

## Consequences
- No migration. No bot in the author's database had a ×N group with a $ amount when this was made, so nothing already imported needs fixing.
- A bot exported from Tracker2 and imported twice gets the same numbers each time; re-exporting it from here writes this app's per-contract numbers.
