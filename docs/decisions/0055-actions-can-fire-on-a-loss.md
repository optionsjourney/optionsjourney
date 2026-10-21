# 0055. Actions can fire on a loss

- **Status:** Accepted
- **Date:** 2026-10-15

## Context
A group's profit actions close part of it and/or move its stop once it has made a set profit (Tracker2's profit actions). The author wants the same on a loss: "at -50%, close 50%", cutting a losing trade in half before its stop. Tracker2 and OptionOmega profit actions fire on profit only.

## Decision
- **A negative At is a loss level.** It is measured like a profit level: a % of the group's entry price (-50 = half of it lost) or $ per contract when the group's target is in $. The action fires once the group's P&L is at or below it, on two checks in a row, like a profit action.
- **Loss rows take their own turn.** Profit rows fire in order going up; loss rows fire in order going down (-25, then -50). The app watches the next of each kind at once, so a loss row isn't held back by a profit row listed before it. One action a check on a group.
- **What a loss row does** is what a profit row does: close a share of the contracts at entry and/or move the stop. A new stop must be a bigger loss than the row's level (or it would fire at once), and the stop is still only ever moved closer.
- All rows together close less than 100%, so at least one contract stays open.

## Why
- The author asked for it, and it reuses the profit action machinery: the close, the stop move and their checks are the same.

## Consequences
- Like profit actions, loss actions run on the app's price watch: only while this computer is on and awake and the app is running.
- Validation: loss levels above -1000% (%), each deeper than the one before.
