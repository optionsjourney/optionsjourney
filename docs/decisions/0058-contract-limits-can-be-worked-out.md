# 0058. Contract limits can be left blank and worked out

- **Status:** Accepted
- **Date:** 2026-10-15
- **Replaces:** the "the user sets" part of [0009](0009-bot-safety-defaults.md)'s size limits. Every bot still has both limits, and every bot order is still checked against them.

## Context
Decision 0009 made every bot carry a most-contracts-per-trade and per-day limit the user types. For a bot sized in contracts they mostly repeat what's already set: per trade is the contracts per entry, per day is contracts × entries. The author asked why they're needed at all. Their real value is as a backstop in the one order check (`trading/check.py`): if the app misfired, say an entry sent twice, the daily cap stops it repeating. Tracker2 has only an optional per-trade cap and no daily one.

## Decision
- **Both boxes are optional.** Blank means the app works the limit out (`definition.trade_cap`, `day_cap`):
  - **Per trade:** the biggest entry the bot makes by itself, its contracts or an entry time's own number, × the largest leg ratio. Sized by buying power: the 100-contract ceiling × the ratio.
  - **Per day:** what the entry times that are on add up to (an entry window: one entry), only the biggest *Most entries per day* of them, never less than one entry.
- **Entries you start yourself** (Trade now, the Trade page, taking over a trade: `schedule.by_hand`) aren't held to a blank limit: each is capped at the 100-contract ceiling × the ratio, has no daily cap, and doesn't use up the bot's worked-out day. A limit you type applies to them as before.
- **Still checked on every order:** the central check gets the worked-out numbers (`definition.limits`), so a misfire of the bot's own entries is still stopped.
- **Stored as 0** in the existing columns: rebuilding the `bots` table to make them nullable would, with foreign keys on, clear `order_log.bot_id`. Existing bots keep their numbers and behave as before. New and imported bots start blank.

## Why
- A friend sizing in contracts shouldn't have to type the same number twice, and a stale limit (raising contracts but not the limit) refused entries for no safety gain.
- Keeping the worked-out cap in the order check keeps the protection that matters: against the app's own mistakes.

## Consequences
- A typo in *Contracts per entry* is no longer caught by a separate limit when the limits are blank: the worked-out limits follow it. Typing a limit still catches it.
- With blank limits, Trade page entries through a bot are bounded only by the 100-contract ceiling and the ticket you review.
