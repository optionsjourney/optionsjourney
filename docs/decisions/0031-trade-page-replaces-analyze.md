# 0031. The Trade page replaces Analyze; you can open trades yourself

- **Status:** Accepted
- **Date:** 2026-10-12
- **Replaces:** [0024](0024-charts-and-analyze-are-tracker2s-pages.md) (its Analyze part; the chart grid stays)
- **Amends:** [0028](0028-one-order-path-and-two-pause-switches.md) ("Only bots open trades in Phase 6")

## Context
A friend the app is for didn't want Tracker2's analyzer. It is a big page built for the author, and it isn't what a broker's trade screen looks like. The spec is 2026-10-12-trade-page-design, in four slices. Slice 2 builds the page; its plan is 2026-10-12-trade-page-slice-2-chain-and-ticket.

## Decision
- **The Trade page** is a chain, an order ticket and the risk graph. It takes Analyze's place in the top bar.
- **Analyze is removed in slices.** Until the last slice deletes it, `/analyze` still loads by its address.
- **Your opening orders go through the same central check** as bots' orders, within **Settings → Your orders → Largest order you can place** (default 10 contracts in one leg). If the setting can't be read, the check refuses.
- **Review order is only a preview** until Confirm arrives in slice 3. The route passes `preview_only=True`, so nothing in slice 2 can send a live order.
- **Rolls are refused** ("close and open separately"). A ticket that closes more than is held goes to the closing checks, not the opening ones.

Changes from the spec's slice list, decided when planning slice 2:
- **The check change and the size setting moved from slice 3 into slice 2.** Without them, Review order on a new trade would always be refused with "Only bots open trades."
- **Working out opening, closing or roll moved into slice 2**, because a preview has to say which one it is.
- **Order quantity follows Tracker2:** the order's quantity is the greatest common divisor of the legs' quantities, and each leg's ratio is its quantity divided by that. There is no separate quantity box.
- **Futures:** the chain and graph work, but Review order refuses them ("Futures orders from the ticket come later"). The page only has DXLink stream symbols for futures options, not TastyTrade's order symbols. Tracked in #93.
- **The working-orders list waits for slice 3**, which brings Cancel and Replace.
- **The review shows the order path's own result message** (for TastyTrade, the buying-power change). Fees aren't shown.

## Why
- One order path means your orders can't skip a protection any bot has. Letting you open trades through a second, looser path was turned down.
- Keeping Review as a preview first lets the ticket be checked against the real broker before anything can be sent.
- Refusing rolls is safer than guessing which legs open and which close. Turned down: previewing a mixed ticket as an opening order.
- The check change had to come early, or the page's one button would never work.

## Consequences
- The nav shows Trade, not Analyze. The Analyze code stays until slice 4, so the chain panel exists twice (`analyzer/` and `trade/`), and Trade's copy lists its changes in `docs/developer/charts.md`.
- The Trade page reuses Analyze's `/api/analyzer/expirations|earnings|chain-table` routes until slice 4 moves them.
- 0028's "Only bots open trades" no longer holds: you can open trades, within the size setting, once Confirm is built.
