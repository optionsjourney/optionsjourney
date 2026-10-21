# 0054. Only a one-leg group rests its stop

- **Status:** Accepted
- **Date:** 2026-10-15
- **Replaces:** the part of [0048](0048-trades-take-either-side-and-each-group-chooses-what-rests.md) that lets a group with one sold leg and other legs (a credit spread) rest its stop on the sold leg.

## Context
Under 0048 a group with exactly one sold leg rests its stop on that leg, whatever else is in the group; after the stop fills, the group's other legs (the wing) are sold now, at a limit, chased or left open. The iron condor and credit spread shapes rest their stops this way. The author doesn't want a group of more than one leg to rest a stop. Tracker2 says the same on its form ("a multi-leg stop cannot rest at the broker"), and suggests splitting the short and long into their own groups and pairing them.

## Decision
- Only a group of **exactly one leg, a sold one**, can rest its stop at TastyTrade (`definition.rests_stop`). A group of two or more legs always works as if it chose **Profit target** (decision 0048): its target rests at TastyTrade and the app watches its stop. Its stop is measured as before (on its sold leg's price for a group with one sold leg, on the group's P&L otherwise), so the stop styles keep their meaning.
- A group of two or more legs has no **Rests at the broker** choice. Its panel says clearly that its stop is watched by the app and works only while the computer is on and awake and the app is running.
- To rest a stop on a short, put it in its own group and pair its wing with **When its stop fills, also close** (slice 21).
- **After a stop, the other legs** no longer applies to a group's own stop: a one-leg group has none, and a watched stop closes its whole group. The same choice stays, as **When another group's stop closes it** (Tracker2's "When pair-closed"), on a group that another group pairs to: sold now, at a limit, chased or left open.
- Trades already open keep the exits they started with.
- When this ships, every **armed or live** bot that has a group of two or more legs is switched off and back to preview, and you're alerted for each, as saving a change that makes a stop watched already does. You re-arm it with the "watched by this app" box ticked.

## Why
- The author asked for it.
- A resting stop on a short that leaves its wing to be dealt with afterwards hides a second step behind one order; a paired one-leg group makes that step visible on the form.

## Consequences
- Iron condor and credit spread bots, as their shapes come, have resting targets and stops the app watches on the sold leg's price. A stop that works with the computer off needs the short in a group of its own.
- The remainder settings (`on_stop_remainder`, `remainder_limit`) stay stored and are read only by a paired close (slice 21).
