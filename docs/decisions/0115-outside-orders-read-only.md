# 0115. Only orders the app sent for you can be changed or cancelled

- **Status:** Accepted
- **Date:** 2026-10-23
- **Replaces:** the part of [0114](0114-trade-ideas-and-pending-orders.md) that made orders placed outside the app changeable. The rest of 0114 (Ideas, the Pending drawer, owners) still stands.

Issue #293.

## Context
Decision 0114 let the Pending drawer and the Trade page's Working orders change and cancel orders placed outside the app (at the broker or in another app). To do that the app rebuilt the order from the broker's copy and refused anything it couldn't send back exactly: linked or complex orders, stock or futures legs, an unusual time in force, conditions, sessions and more. It also held an outside order back while an earlier attempt of the app's might be that very order. That is a lot of rules for orders the app knows little about, and every one is a way to get a change wrong. The author decided it isn't worth it before it shipped.

## Decision
- **Only an order the app sent for you (owner "you") can be changed or cancelled**, in the Pending drawer and on the Trade page. It is rebuilt from the app's own order log, as before 0114.
- **Every other order is listed but read-only**, with the reason shown:
  - Outside the app: "Placed outside the app. Change or cancel it at your broker."
  - A bot's or a managed trade's: "Managed by …, use its controls." (unchanged)
  - Unknown (the app's tag but no log row): unchanged.
- Who owns an order is still read again at every Cancel and Replace, and the order is still read fresh from the broker first.
- The broker's copy is no longer rebuilt, so `core/pending.rebuild` and the order terms it needed (`complex`, `option_legs_only`, `resendable`, `order_type`, `time_in_force`, `trigger` on `BrokerOrder`) are gone. The broker's signed price stays, to show it for orders with no log row.

## Why
- An order placed elsewhere is best changed where it came from: that place knows all its terms.
- It removes the rebuild, its refusal list and the hold on outside orders, all of which existed only for this.
- It matches what the Trade page did before 0114.

## Consequences
- The "Outside the app only" filter in Pending still works; those orders just have no buttons.
- The guide, FAQ and the Pending ⓘ tip say only your own orders sent from the app can be changed or cancelled.
- The live check of Change price on an outside order that 0114 left to do is no longer needed.
