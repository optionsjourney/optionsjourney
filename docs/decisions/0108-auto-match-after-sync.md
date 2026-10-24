# 0108. A sync matches new trades to their bot on its own

- **Status:** Accepted
- **Date:** 2026-10-23

## Context
Trades placed outside the app, for example by bots still running in another app, sync into the journal under generic names such as "SPX Double Calendar". **Match trades** and **Match all bots** (decision 0068, #169) can link them to the right bot, but only when the user presses the button. Each day's new trades stay generic until then.

Tracker2 files a trade under its strategy as the transaction is imported, scoring it on account, symbol, shape, time and day.

## Decision
- **After every sync that brings in or changes trades**, the app links the trades that **Match all bots** would tick for you: a trade on the bot's account and underlying, with the bot's legs, opened within 2 minutes of one of its entry times, that matches that bot and no other as well. It's the same scoring and the same filing (tag, link and the bot's name if the trade has no name of its own) as Match all bots.
- **Ties and guesses are left for the user:** a trade that two bots match equally, or that matches a bot with no entry times (legs alone), isn't linked on its own.
- **A trade carrying another bot's tag** is left alone.
- **Taking a trade off a bot sticks:** a trade you unlink, or whose match you undo, is recorded (`bot_auto_skips`) and never linked on its own again. Match trades and Match all bots still offer it.
- No setting to turn it off: Unlink is the way out, as in Tracker2, which always matches.

## Why
- The rule is exactly what the user already accepts by pressing Match selected on the pre-ticked trades, so nothing is linked that the user wouldn't have been offered ticked.
- Remembering unlinked trades stops the app from undoing the user's choice on the next sync.

## Consequences
- The first sync after updating also links any older trades that fit, not just new ones.
- An auto-match has no Undo button; Unlink on the trade takes it off.
- A failing auto-match is logged and never fails the sync.
