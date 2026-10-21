# 0072. Tags only: strategies are retired, and a bot files its trades under a tag

- **Status:** Accepted
- **Date:** 2026-10-18
- **Replaces:** [0019](0019-journal-strategies-tags-dashboards.md)'s "one strategy per trade" (its one list, free tags, filters and dashboards stand) and [0025](0025-strategy-tags-and-stocks.md)'s "strategies also tag their trades" (its Stocks tag and tab stand). Where [0068](0068-match-trades-to-bots.md) and [0071](0071-matched-trades-link-to-their-bot.md) say "strategy", read "the bot's tag".

## Context
Tracker2 files every trade under one strategy (subcategory) and adds free tags on top, and 0019 followed it. Since 0025 choosing a strategy also adds a tag of the same name, so most trades carry the same word twice, as a strategy and as a tag, and filtering already runs on tags. For friends who aren't technical, "is this a strategy or a tag?" is a question with no useful answer. Option Omega uses tags only and lets the user build the views they want from them; the author finds that works well and limits less.

This is a deliberate break from Tracker2.

## Decision
- **No more strategies.** A trade has only tags. Migration 0043 turns every strategy into a tag of the same name (reusing one that exists), makes sure every trade filed under a strategy carries that tag, and then drops `trade_edits.strategy_id` and the `strategies` table.
- **A bot files its trades under one journal tag** (`bots.tag_id`, required), in place of its strategy. A run's trades and trades matched to it get that tag. Its record is the trades linked to it on its account, plus trades with its tag on its account that no bot is linked to (0071's rule, tag for strategy). The bot form picks the tag from the journal's tags or makes a new one. A bot's own labels ([0049](0049-the-bot-tag-picker-offers-the-journals-tags.md)) stay separate.
- **A tag a bot files under can't be deleted** while that bot exists, as a strategy couldn't.
- **Filters and dashboards** filter by tags only. A saved dashboard that filtered by a strategy filters by its tag instead; "no strategy" becomes "no tags".
- **The Strategies page becomes a Tags page:** add, rename and delete tags.
- **Starter tags** replace starter strategies in setup (0DTE, Credit Spreads, Iron Condors, Calendars, Wheel).
- **Breakdowns by tag** list each tag's trades and P/L. A trade with three tags shows under all three, so these rows aren't added up; every total on a page comes from the trades it shows, each counted once.
- **Bot files from Tracker2** map their subcategory to a tag where they mapped it to a strategy.

## Why
- **One kind of label** is simpler, and the user decides which views matter by the tags they choose.
- **Nothing is lost:** every strategy already had a tag (0025), and a bot's record already follows links first (0071).
- **No double counting in totals:** totals come from the trades shown, never from adding up tags.

## Consequences
- Going back would need a new decision and migration; the migration's downgrade rebuilds strategies from the bots' tags only.
- The "P/L by strategy" view becomes "P/L by tag", whose rows can overlap.
- An unlinked trade carrying two bots' tags on the same account counts in both bots' records (a trade had only one strategy before). Each record is right on its own, but bot records shouldn't be added up; Match trades lists such a trade apart, and matching it links it to one bot, which ends the overlap.
- A bot's tag is added to its trades when the journal is built, so it can't be removed from them on the trade page.
