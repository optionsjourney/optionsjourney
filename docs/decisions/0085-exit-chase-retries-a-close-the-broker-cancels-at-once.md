# 0085. The exit chase sends a close again once when the broker cancels it at once

- **Status:** Accepted
- **Date:** 2026-10-20
- **Refines:** the bot exit chase (`services/bot_chase.chase`), which until now never sent an order again once it was gone. [0047](0047-per-trade-settings-and-closing-another-group-after-a-stop.md)'s "never retried from the monitor" is unchanged: this retry happens inside the one chase, never later.

## Context
Tracker2 saw TastyTrade cancel a marketable closing order a fraction of a second after it arrived, with no reason given, and its chase gave up, leaving the long leg open (Tracker2 commit e7ab1a95). This app's chase did the same: a first read showing the order cancelled with nothing filled ended the chase as "missed". Tracker2 also found that a replace TastyTrade accepts can be rejected afterwards because the old order filled first ("Received a fill on the original order"); the chase only read the new order and reported a failed close while the leg had in fact sold (Tracker2 commit 8dce236f). Issues #198 and #199.

## Decision
- **One retry, as Tracker2.** When the chase's first order reads back *cancelled* on its very first read, with nothing filled, not replaced from the broker's own app, not a market order, and while its market is open, the same order is sent again once at the same price. It goes through the central check like any order (preview vs live, size limits, Stop all bots), so a refusal there ends the chase. Its read does not use up one of the chase's tries. A rejected order, a cancel seen on a later read, or a second cancel at once is not retried.
- **A dead replacement looks at the order it replaced.** When a replacement turns up gone with nothing filled, the chase reads the order it replaced once more, read-only. If that one filled, the close counts as filled at its price; if it part-filled, as partial. Otherwise the chase ends as before.

## Why
- Nobody in the app cancels a chase's order without reading it first (a stop request is checked before the read), so a cancel on the first read is the broker's doing, not the user's.
- One retry at the same price is the smallest step that gets a stuck close done; more retries could chase a broker that is refusing for a reason it hasn't said.
- Reporting a fill that happened is safer than a false "close failed" alert that invites the user to close a leg that is already gone.

## Consequences
- In the rare case a user cancels the order from the broker's own app within the first interval, the bot sends it once more. The second send can be cancelled the same way, and is not sent a third time.
- After the regular session (or outside futures hours) the retry is not made: a close the broker drops then is reported as before.
