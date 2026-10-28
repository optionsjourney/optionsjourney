# 0122. Min short width and Keep trying have on/off switches

- **Status:** Accepted
- **Date:** 2026-10-28

Issue #315. Replaces the "blank is off" part of [0121](0121-keep-trying-then-a-fallback-premium.md).

## Context
Both settings were turned off by leaving the box blank. Since 0121, typing a Min short width also makes the Fallback column appear in Legs, with red boxes, in another part of the form. Turning either off meant losing the number.

## Decision
- Each gets a switch, as Blackout days and the filters have. On shows its box (and, for Min short width, the Fallback column); off hides it and keeps the number, unused.
- The switches are stored on the bot and are on by default, so a bot or a bot file with a number from before the switch keeps using it. A blank number is off either way.
- A switch that's on with no number won't save: "Enter the min short width, or switch it off."

## Why
- The Fallback column appears because you chose to, and you can pause either rule for a day without retyping.
- Every optional rule on the form now works the same way.

## Trade-off
- Two more stored settings, and a bot file now carries both switches.
