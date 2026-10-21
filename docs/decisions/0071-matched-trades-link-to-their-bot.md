# 0071. Matched trades link to their bot, and a bot's record follows the links

- **Status:** Accepted
- **Date:** 2026-10-18
- **Replaces:** the "filing under the strategy is all a match is" part of [0068](0068-match-trades-to-bots.md). Its scoring, ticking, Match selected and Undo rules stand.

## Context
Decision 0068 made a match only a strategy filing, since a bot's record was every trade under its strategy on its account. That works when each bot has its own strategy, as in Tracker2 (one subcategory per bot). Many users share a strategy between bots (for example 26 calendar bots under "Calendars"), so a matched trade never showed which bot it belongs to, and every bot under the strategy counted every other's trades (#169).

## Decision
- **Match selected links the trade to the bot** in a new table, `bot_trade_matches` (position key, bot, when), like Tracker2's `BotPosition` link but with no bot run. It still files the trade under the bot's strategy and tag, and still splits a whole condor for a bot that splits. A condor split on match is linked by its whole key; its halves find the link through `split_from`, as a run's link does. Undo puts the links back as they were (removed, or back to the bot it had).
- **The Journal shows a matched trade's bot** ("bot · name", linking to the bot's record) but offers no run actions: no Manage chip, no bot chips. Without a run it can still be handed to a bot or managed with Manage this trade.
- **A bot's record** is the trades linked to it on its account (the runs it placed and the trades matched to it), plus trades under its strategy on its account that **no bot** is linked to. A trade linked to another bot is that bot's, even under the same strategy.
- **Match trades** (one bot) offers trades filed under its strategy without a link, so trades filed the 0068 way can be linked, and lists a trade matched to another bot apart, saying "matched to X"; ticking it moves the link.
- **Match all bots** (the Automation list) offers every trade no bot is linked to under the live bot (not Paper, archived, deleted or a manual bot's hidden copy) it scores best against. A strong match to one bot is ticked; a tie at the top score is listed under the first, names the others and is left unticked. One Match selected and one Undo cover the whole batch.

## Why
- **Follow Tracker2 where it settles it.** Tracker2 counts a bot's stats by subcategory and account (`services/bots/strategy_stats.py`), but every Tracker2 bot has its own subcategory, so its record is in effect the bot's own trades. Here bots share strategies, so the link is what says whose a trade is; the strategy fallback keeps Tracker2's rule for the trades you open by hand.
- **One link per trade.** A trade belongs to at most one bot, by a run or a match, so two bots never share it.

## Consequences
- A bot-placed trade you re-file under another strategy still counts for its bot.
- The record page's "see in Journal" filter is still strategy and account, so it can show a few trades another bot owns.
- Undo lasts until the app restarts, as before.
