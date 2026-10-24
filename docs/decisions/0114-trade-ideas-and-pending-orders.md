# 0114. Trade Ideas, and a Pending drawer for every working order

- **Status:** Accepted (changing and cancelling orders placed outside the app replaced by [0115](0115-outside-orders-read-only.md))
- **Date:** 2026-10-23

Issue #292. Design: the spec. It replaces nothing, but it changes one earlier behaviour: orders placed outside the app, which the Trade page's Working orders showed read-only, can now be changed and cancelled.

## Context
Two things were missing:
- **No way to keep a trade you built but didn't send** and see later how it would have done.
- **No single place to see every order still waiting at a broker.** The Trade page's Working orders list showed one view of them, and orders placed at the broker or in another app were shown read-only.

## Decision
**Ideas**
- **Idea ☆** on the Trade page ticket saves the ticket (legs, quantity, entry price and a label and note) to a new `ideas` table (migration 0055). An idea is one ticket. Cards are stored as a list so several can be added later.
- The entry price is the Limit if you typed one (locked), else the live mid. An idea's P/L is (entry − current mid) × quantity × multiplier. Expired legs use intrinsic value at the official settlement. Once every leg is settled the P/L is stored once and never priced again. Status (Live, Partly expired, Expired) is computed, not stored.
- The Journal's **💡 Ideas** drawer lists them, newest first. **Trade it** opens the Trade page with the ticket filled in and the price locked, for a live idea only.
- **An idea is retired when the broker accepts the order** (`orders.submit` returns ok with action send), or when a bot takes the hand-off for a bot entry. A refused order, a preview or an unclear outcome keeps it. A failed delete never fails the trade.
- Not ideas: stock, VIX and futures-option legs.

**Pending**
- The Journal's **⏳ Pending** button and drawer list every working order in every active TastyTrade and Schwab account, one section per account. A failing account shows its own error and never hides the others.
- **Each order has an owner:** you (sent from the app), a bot, a managed trade, **Outside the app**, or **unknown**. Unknown is an order the app tagged or may have sent but can't match to a log row; it gets no actions.
- **You and Outside orders can be changed and cancelled; bot and managed orders can't.** Changing those would fight the bot's exits or chase. They name the bot or trade and link to it.
- **An outside order is rebuilt from the broker's own copy** (`core/pending.rebuild`). Change price is refused, with the reason shown, when the order is part filled (cancel only), linked or complex, has stock or futures legs, has a time in force the app can't send, isn't a limit order, or has no credit or debit. Cancel is always allowed.
- **No actions while the app is still checking an earlier order** on the same legs, or while the broker is already changing the order.
- Both views (Pending and Working orders on the Trade page) read the same service, so they follow the same rules. Change price and Cancel go through `services/orders.replace` and `cancel` (origin "you"): the check, size limits, the broker's preview, Freeze and the order log all still apply.
- Polling only: the list every 30 seconds while open, the count every 5 minutes, live mids every 2 seconds from prices the app already holds.

## Why
- Retiring on acceptance matches how a person thinks of it: the order is at the broker, the idea is spent. A follow-up status read would add a second path with its own failures.
- One rule for outside orders in one place is simpler than a Pending-only exception, and the author asked for outside orders to be changeable.
- Rebuilding from the broker's copy, and refusing what can't be rebuilt exactly, means a change never drops a term of the order silently.
- Treating "may be ours but can't prove it" as unknown, with no actions, keeps a bot's order from being changed by hand.

## Consequences
- Outside orders are now changeable on the Trade page too. The guide's old "shown but can't be changed" line is gone.
- Some outside orders are briefly or permanently cancel-only (see the refusals above). Better that than a replacement that differs from the original.
- A broker that rejects an order a moment after accepting it still removes the idea.
- A live check of Change price on an outside order at each broker is still to do.
