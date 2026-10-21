# 0068. Match past trades to bots by filing them under the bot's strategy

- **Status:** Replaced by 0071 (in part: a match now also links the trade to the bot, and a bot's record follows the links)
- **Date:** 2026-10-17

## Context
After importing bots (often from Tracker2), the Journal already holds months of trades those bots placed, but nothing ties them to the new bots, so each bot's record starts empty. Filing each trade by hand is slow (#164).

## Decision
- A bot's **Match trades** page lists the journal trades that look like the bot's, and files the ones the user ticks under the bot's **strategy**, the same way the Journal's strategy picker does (the strategy and its tag). A bot's record is already every trade under its strategy on its account, so that is all a match is: no link table, no made-up bot run, no bot badge.
- Scoring follows Tracker2's `strategy_matcher._score_match` (`core/bot_match.py`). Hard filters: the bot's account, underlying (SPX also matches SPXW), leg shape (sides, calls or puts and ratios; strikes and days to expiration ignored; a split iron condor's half matches the bot's side), within **2 minutes** of an entry time when the bot has any (Tracker2 is exact on the minute; bots chase), and on the bot's days when it trades on chosen days. Weights: account 1, shape 2, time 3, day 1.
- A match is ticked by default only when the bot has entry times to check; a shape alone is listed unticked.
- Left out: trades a bot placed, stock trades and trades already in the bot's record. Trades under another strategy are listed apart, unticked; ticking one moves it.
- Nothing is filed until **Match selected** is pressed. **Undo** puts back each trade's previous strategy and tags for the last batch; it is kept in memory until the app restarts (any trade can still be re-filed in the Journal).

## Why
- **Follow Tracker2**, which classifies trades by the same rules.
- **One way a trade belongs to a bot.** Reusing the strategy keeps the record, the Journal's filters and the bot's stats in step, with no second link to keep right.
- **Ask, don't guess.** Matching on shape alone can be wrong, so nothing changes without the user's say.

## Consequences
- No background matching on sync, and matching doesn't make a bot manage a trade (Hand to a bot and Manage this trade do that).
- Undo is lost if the app restarts; re-filing in the Journal still works.
