# 0019. The journal: one strategy, free tags, and dashboards

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
Tracker2 organizes trades into fixed pages: 0DTE, Calendars and Options. Those categories fit the author's trading, not a friend's. The author wanted the journal to work more like Option Omega: show everything, and let the user slice it with their own labels.

## Decision
- **One list:** the journal shows **every trade, open and closed**, in one list, and it's the app's home page.
- **One strategy per trade** (MEIC, Wheel, Earnings, …), chosen from a list the user manages. A bot's trades will get the bot's strategy automatically ([0004](0004-automation-first-strategy-naming.md)). One strategy per trade means strategy P/L never double-counts.
- **Any number of free tags** per trade ("earnings", "mistake", "A+ setup"). They're created simply by typing them on a trade.
- **Filters:** strategy (or "no strategy"), tags, account, status and period. Bot filters arrive with the bots.
- **Dashboards are saved views:** a name plus a set of filters, shown as tabs above the journal. "All trades" is always there, and the user adds, updates and deletes the rest. Each dashboard shows summary stats (realized P/L, trades, win rate, average, best/worst) above its trades. The P/L calendar and charts get added to dashboards in the next step.
- **Per-trade edits:** name, strategy, tags, notes, close reason, and Split / Combine for iron condors ([0018](0018-iron-condor-split-is-a-choice.md)). A blank field means "use the default".
- **Starter strategies:** 0DTE, Credit Spreads, Iron Condors, Calendars and Wheel, until the setup wizard asks about the user's trading style ([0007](0007-guided-setup-wizard.md)).

## Why
- **Each friend's journal fits their own trading,** with no categories baked in.
- **Two kinds of label:** a single strategy answers "how is this strategy doing?", and tags answer everything else.
- **Fixed pages become optional:** saved views give Tracker2's convenience without forcing its layout.

## Consequences
- **Edits are keyed by position.** They're stored against the position's stable key ([0017](0017-positions-follow-tracker2.md)), so they survive re-syncs and rule fixes.
- **Split halves have their own keys** (`…#put`, `…#call`) and their own edits.
- **Deleting a strategy keeps the trades:** they simply have no strategy.
