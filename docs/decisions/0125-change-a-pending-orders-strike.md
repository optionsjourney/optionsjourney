# 0125. A pending order of yours can have an opening leg's strike changed, after a preview

- **Status:** Accepted
- **Date:** 2026-10-30
- **Refines:** the Pending drawer's and Working orders' Change price (#292, #293). Who can change what is unchanged: only orders you sent from the app.

## Context
Tracker2's Pending drawer can now change the strike of an opening leg, not just the price (commits 44173488, ec579d7e, 41c6e0cb). Here an order's strikes could only be changed by cancelling it and making a new one, which loses the order's place for a moment and means typing it all again. Issue #321.

## Decision
- **Only an order of yours that opens a trade, on listed options.** A closing order, a futures option, a bot's order, a managed trade's order and an order placed outside the app get no strike boxes, and the server refuses them too.
- **Only the strike moves.** The new symbol is built on the server from the leg's own symbol, changing only its strike. Expiration, put/call, buy/sell, ratio and quantity stay. The page never sends a symbol.
- **Preview before Confirm.** New strikes are first checked by the broker (a dry run through the same order path, nothing sent). Confirm is offered only while the strikes and price are exactly what was previewed. A price-only change works as before.
- **One replace.** The changed order goes out as a replace through `orders.replace`, with every check a new order gets.

## Why
- Following Tracker2, which settled on the same limits and the same preview-then-confirm.
- A wrong strike is a different trade, so the broker's own check comes before the send.
- Tracker2 had to work around its library dropping the legs on a TastyTrade replace. This app already sends the whole order on a replace, so no workaround is needed.

## Consequences
- Tracker2 also lets an outside order and a waiting roll have their strikes changed. Neither is done here: outside orders stay view-only, and there are no waiting rolls.
- The dialog doesn't price the new strikes before the preview (Tracker2 shows their mid). The broker's preview message is what you see.
