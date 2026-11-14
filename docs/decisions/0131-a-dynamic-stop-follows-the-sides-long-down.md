# 0131. A Dynamic stop rests at the credit plus the side's long less a buffer, and only moves down

- **Status:** Accepted
- **Date:** 2026-11-14
- **Adds to:** 0036 (the closing-order overlay), 0048 (which exit rests), 0054 (a side of two legs rests its target), 0061 (paper exits).

## Context
An iron condor's side is meant to stop when the side (sold leg minus bought leg) is worth about the whole condor's credit. The closing-order overlay compares the sold leg **alone** to the credit. With an expensive wing it stops far too early: a 3.30 condor with 1.00 wings put both sides' stops at 3.10, only 0.20 above a 2.90 short. Late in the day the opposite happens: the wing is worth nothing and a % stop is looser than the credit. Tracker2 added a "Dynamic stop" for this (its commits c682c65b to 904365cc, with fixes for stale quotes, re-saves, refused re-places and SPX's tick tiers). Issue #329.

## Decision
**What it is.** A fourth value of the bot's closing-order mode, `stop_co_mode = "dynamic"`, with `stop_co_amount` as the **buffer** ($0 or more; the other modes still need more than $0). Each side's stop on its sold leg is `(entry credit + the side's long − buffer) / the short's ratio`, rounded down once to the short's tick. It **replaces** the side's own style; it isn't an overlay. The long is priced at its fill when the stop is placed.

**The ratchet** (`services/bot_dynamic_stops.py`, a step of the 30-second bot monitor, at most once a minute, regular session only, not under Freeze): it prices the long at its live mid and moves the stop **down only**, by at least one tick, never to a level at or below the short's ask (it goes to the first tick above the ask instead, on the tick for that price: above an SPX ask of 3.00 is 3.10, never 3.05). A resting stop moves by **one replace** through `orders.replace`, so every move passes the central check (preview vs live, size limits, Stop all bots, Freeze) and there's no moment without a stop. Moves are quiet (the stop's log and arithmetic, no alert).

**Choices this app had to make** (Tracker2 doesn't settle them):
- **Which long.** This app's condor keeps each wing in its short's group, while Tracker2 (and imported bots) keep it in a separate group named by "close on stop". The long is the group's own bought legs; if it has none, the bought legs of its close-on-stop group; else 0, which the stop's arithmetic notes. A long group that has closed counts 0.
- **Which stops.** Working stops resting at the broker, and stops the app holds on the short. Tracker2 has only the first kind, but this app's default condor rests its target and holds the stop on the short (decision 0054). Without the second kind the feature would do nothing on the default shape. A held stop's move only changes its level, and leaves the row's `updated_at` alone (the watched stops' lapse check reads it).
- **What it leaves alone.** A $ stop keeps its dollars and a group watched on its P&L keeps its own stop (as the overlay does). On a debit fill it's ignored and said, and a group still needs a stop value to get a stop.
- **Ownership.** The ratchet moves a stop only while its latest logged arithmetic is a Dynamic stop's. A profit or time action, Change stop or another closing-order mode takes it over, as in Tracker2.
- **Fresh quotes.** The ratchet reads the quote stream through `bot_exits._quote`, which returns nothing older than 30 s. There's no cached fallback of any age (the bug Tracker2's 1a87ccec fixed), and the strict 30 s is kept rather than the watched stops' "quiet while the stream is live", because a stale low long tightens a stop the ratchet can never undo, while a skipped move costs nothing. Every long leg needs bid ≥ 0 and ask > 0, not crossed: 0/0 is no market, not a free option. The short needs a fresh ask above 0, else there's no move.
- **Placement and re-arm never fire at once** (Tracker2 1a87ccec). When a new Dynamic stop, or one re-armed at its latest (ratcheted) level, is at or below the short's fresh ask (with no ask, at or below its fill), it rests one tick above that instead of being refused or firing. A trade handed to a bot keeps that path's own check: through its stop, it gets none and Needs you. Restore and switching a stop back on use the row's latest level, as before.
- **The run page's switch** (Tracker2's drawer). **Dynamic stop** in the closing-order list (`bot_manage.set_closing_order`) re-sets the stop **once** from the long's live mid, and may loosen it. It is refused, with nothing changed, without a fresh long quote or when the short's ask is through the level. Saving the same buffer on a side whose Dynamic stop rests changes nothing (it never undoes the ratchet). A switch moves the stop the way every run-page change does (cancel, confirm, place). The ratchet's moves replace.
- **A refused or unclear replace** leaves the row and the old order as they were. The monitor re-arms a stop that's gone, and follows an order replaced at the broker (decision 0124). It alerts once per stop per day, and the next minute tries again.
- **Brokers.** TastyTrade and Schwab both. Tracker2's dynamic stop has no broker check, and its replace works on both. Futures bots have no stops, and validation refuses Dynamic stop there ("turn off Dynamic stop").
- **Paper bots: modelled, not refused.** Paper's moved stops were already 0-contract "action" rows, so each ratchet move is one row marked `dynamic`, using the same pure rules and freshness. A paper stop that starts at or below the short's fill starts one tick above the pretend fill, since there's no ask at the fill to go by.
- **No migration.** Both mode columns are plain `String(10)` with no CHECK.

## Why
- Following Tracker2's behaviour keeps the two apps' stops the same for the same trade. Every choice above either maps its rule onto this app's groups, or picks the safe side where only this app has the question (held stops, quote age, paper).
- A replace keeps the side protected through every move, and going through the order path keeps the one central check.
- Down-only, a full tick, fresh quotes and never at the ask are what make an automatic move safe. Only a deliberate switch may loosen.

## Consequences
- Bots that don't pick Dynamic stop are unchanged.
- A stop row's log grows by one entry per move (about one per price step the long falls).
- Changing the bot's mode affects new trades only. Open trades change only through the run page's switch, and a resting Dynamic stop keeps ratcheting after the bot is switched away from it.
