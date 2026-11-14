# 0124. A stop or target replaced at the broker is followed, not put back

- **Status:** Accepted
- **Date:** 2026-10-30
- **Refines:** re-arming a stop that disappears, and decision [0048](0048-trades-take-either-side-and-each-group-chooses-what-rests.md)'s alert for a resting target that ends. Bots slice 21 already follows a resting *entry* replaced at the broker; this does the same for exits.

## Context
TastyTrade has no "Replaced" status. When a resting order is changed (in TastyTrade's app, say), the old order reads *Cancelled* and names the order that replaced it. Tracker2 took such a target for a cancel and placed it again. TastyTrade refused the second closing order (`cannot_close_against_more_than_existing_position`), and the live new order went untracked (Tracker2 commit 2ecf0784). Here a stop would be re-armed the same way, and a target would be marked failed with an urgent "no exit" alert, while Restore would place a second one. Issue #318.

## Decision
- A resting stop or target that reads gone with nothing filled, and names a replacing order, is not cancelled or re-armed. The monitor reads the replacing order.
- If every leg of it closes this exit's own options, the exit follows it: the new order id is tracked, a target takes the new order's price, and a routine alert says so. The next pass reads it like any other.
- If it can't be read, or doesn't match, nothing is placed: the exit is marked failed, the run Needs you, with an urgent alert.

## Why
- Putting the old order back over a live replacement is either refused or doubles the closing size. Following is what Tracker2 settled on.
- The legs check keeps the app from adopting an order that isn't this exit's (anything else is left to the user, as the entry follow does).

## Consequences
- A followed stop keeps the trigger the app set on its row: the order read doesn't show a stop's trigger, so the run page may show the old one.
- A replaced order that partly filled is not followed; it's handled as before.
