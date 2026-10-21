# 0018. Splitting an iron condor is the user's choice

- **Status:** Accepted (the per-trade button arrives with the journal page; the bot setting arrives with the bots)
- **Date:** 2026-10-09

## Context
Tracker2 can split an iron condor into a put spread and a call spread, tracked as two positions with separate P/L. It does this automatically for 0DTE iron condors whose strategy configuration has `split_ic` turned on. OptionsJourney dropped those configurations ([0004](0004-automation-first-strategy-naming.md)). The author said the split should be decided by the user, not by a fixed rule.

## Decision
- **The bot decides by default:** splitting is a **bot setting**. Each bot's configuration says whether the iron condors it opens are tracked as one position or as a put spread and a call spread. The bot's own trades carry its identity, so the app knows which setting applies.
- **Per-trade override:** a **Split / Combine** button on any iron condor in the journal overrides the bot's setting, or decides for a trade no bot placed.
- **No 0DTE rule:** there's no built-in restriction like Tracker2's "0DTE only".
- **Default:** a trade with no bot setting and no override stays one position.

## Consequences
- **Storing the choice:** the per-trade choice is saved against the position's stable key ([0017](0017-positions-follow-tracker2.md)), alongside the other journal edits.
- **Building it:** the position builder takes the split choices as input and builds two positions for a split iron condor. It runs on read, so changing a choice re-applies to that trade's whole history straight away.
- **One close, two halves:** a closing order covering both halves of a split iron condor (all 4 legs) closes both. This is the one exception to "one closing order closes one position".
