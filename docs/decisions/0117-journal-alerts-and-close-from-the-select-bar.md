# 0117. The journal's select bar alerts on and closes trades; tagging moves to each trade's page

- **Status:** Accepted; its Limit/Market choice for Close replaced by [0118](0118-journal-close-has-no-market-choice.md)
- **Date:** 2026-10-25

Issue #300.

## Context
The journal's select bar could add and remove tags on the ticked trades, and graph them together. Alerts could only be set from a trade's own page, one trade at a time, on its open P/L in dollars. Closing several bot trades at once was on the Automation page only, and only for 0DTE trades.

## Decision
- **A bell on every open trade**, next to the chart icon and the gear, opens an alert panel in the journal's side drawer. The panel follows the original app's Alert modal: open P/L in $ or **% of entry** (refused under $50 of entry), or the underlying's price with the trades' strikes to pick from. It also lists the alerts already set and has an **In the app only** choice. The bell is green while an alert waits and amber once one has gone off.
- **Alert in the select bar** sets one alert on the ticked trades' **combined P/L**: open trades, one underlying, at most 10. Its value is each trade's realized P/L plus its open P/L, so a trade that closes still counts. While any open trade is unpriced it isn't checked.
- **% of entry is turned into dollars when the alert is set** (from the trades' net entry, rolls included). No new column: a later roll doesn't move the level.
- **Close in the select bar** closes each ticked trade that a bot or its own Manage-this-trade rules manage. Each is a Close entire trade, by its own exit chase, after one confirm step. Trades nothing manages are skipped and named. Ticking one side of a split iron condor closes only that side.
- **The journal's Close takes any expiration.** It is the same close the trade's own panel already offers for any expiration. The original app's Close selected (and this app's Automation page) is 0DTE only, and stays so there. Market is still 0DTE only, as for every close.
- **The bar's Add tags / Remove tags / Save are removed.** Tags are edited on each trade's page. A bot's Match still tags many trades at once.

## Why
- The bell and the combined alert follow the original app's journal alerts: an alert is set where the trade is looked at, and two trades opened together (a put side and a call side) can be watched as one.
- Closing from the journal is where people look at their trades. Following each trade's own exit rules keeps every order going through the same checks as a single close.
- The tag fields crowded the bar, and are rarely used next to the new actions.
