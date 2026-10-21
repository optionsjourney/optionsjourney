# 0004. Automation first; simple strategy naming

- **Status:** Accepted, amended by [0021](0021-product-scope.md)
- **Date:** 2026-10-09

## Context
Tracker2 labels each trade with a strategy using a rule matcher. Each strategy is a rule ("SPX, PUT side, entered at 09:32 on a Monday, for a net credit…") stored in a configuration table. It works for the author, but two things make it a poor fit for friends:

- **Too much setup:** someone has to write rules before anything gets labeled.
- **Too tied to one person's style:** the categories and names (0DTE, `wheel_tbill`, BIC, MEIC…) only make sense to the author.

Also, **the main point of this app is automation.** Most trades will be placed by bots.

## Decision
- **Trades placed by automation:** the bot knows what it is, so it stamps its own strategy name on every trade it places. This applies to the app's built-in bots and, where possible, to trades recognized from outside auto-trading tools. No rules are needed.
- **Manual trades:** these get an automatic **default name based on their structure**, such as "SPX Put Credit Spread", "TSLA Short Put" or "SPX Iron Condor". If the user wants, they can move a trade into their own category with one click.
- **The rule matcher is dropped.**

## Why
- **Labels come for free:** for automated trades, classification falls out of the bot that placed the trade.
- **Structure-based names need no setup** and are understandable to anyone.
- **Users' own categories stay optional:** they're there when wanted, never required.

## Consequences
- **We keep Tracker2's leg-shape detection,** the code that recognizes verticals, iron condors, calendars and butterflies, and use it to build the default names.
- **Every order a bot places must carry the bot's identity,** so its trades can be matched back to it.
- **Recognizing trades from outside tools** depends on what those tools leave on the order, such as a tag or order note. This needs research per tool.
- **Tracker2's predefined categories are replaced.** The setup wizard asks a few questions about trading style and creates starting categories from the answers.
