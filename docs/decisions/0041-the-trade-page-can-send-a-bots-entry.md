# 0041. The Trade page can send a ticket as a bot's entry, and any shape can be handed to a bot

- **Status:** Accepted (the author, 2026-10-13: live bots only, hand-over of trades opened today only, and Tracker2's lowest-strike pairing, shown before you confirm)
- **Date:** 2026-10-13
- **Amends:** [0031](0031-trade-page-replaces-analyze.md) (the Trade page gains a second way to send) and [0038](0038-a-trade-you-opened-can-be-handed-to-a-bot.md) (hand-over is no longer iron condors only)
- **Builds on:** [0028](0028-one-order-path-and-two-pause-switches.md) (one order path), [0039](0039-any-same-day-structure-with-a-stop-that-always-works.md) (any structure), [0040](0040-legs-in-other-expirations-and-trades-held-overnight.md) (held trades)

## Context
Tracker2 lets you pick strikes in its Analyzer and press **Trade** to have a bot place the entry and manage the exits (`analyzer_entry.run_analyzer_entry`, spec `2026-09-27-analyzer-bot-trade-design.md`). Slice 12 left it out as a product choice and recommended a design for the Trade page (decision 0031). The author approved that design on 2026-10-13 (#98), together with widening hand-over (slice 12) beyond same-day iron condors. The spec is 2026-10-13-bots-slice-15-manage-with-a-bot-design.

## Decision
- After **Review order** is accepted, the Trade page offers **Manage with a bot**: the live bots on the ticket's account whose legs match the ticket's (side, type and ratio; Tracker2's `analyzer_shape.match`, pairing by lowest strike, with each group's legs shown before you send). Expirations and strikes come from the ticket.
- **Sending it makes it that bot's entry.** The order goes through the one order path as origin `bot`, within the bot's Max per trade, Max per day and Max open trades, and under Freeze, No new trades, the regular session and the trading guard. The run is claimed on a key from the page's one-time token before any broker call, so a second press places nothing.
- The entry is **chased by the bot's own entry chase**, starting at the app's fresh mids, and every fill gets its exits exactly as a scheduled entry's.
- **The held rules are read on the trade's real expirations:** no reverse calendar, a timed close for any group in more than one expiration, Schwab same-day only, nothing past 60 days, only the weekly root, and a trade held past today only to a bot that holds overnight.
- **Hand-over takes any shape a bot can hold**, opened today, under the same match and held rules, including legs in more than one expiration.
- The plain ticket (Review, Confirm, Cancel, Replace) is unchanged.

## Why
- A trade you set up by eye can still get a bot's protection from the first fill, without a hand-over afterwards.
- One matcher and one set of held rules serve both ways in, so a bot never takes a trade it couldn't have opened itself.
- Claiming before any broker call is what makes a double click safe (Tracker2's token).
- Turned down for now:
  - **Tracker2's Limit mode** (a resting GTC entry with Modify and Cancel). It needs its own monitor and cancel paths; the chase reuses everything a scheduled entry has.
  - **Offering bots in preview mode.** They'd send nothing; the author asked for live bots.
  - **Hand-over of trades opened on an earlier day** (Tracker2 allows it). Kept to today until the held-trade checks have run for a while.

## Consequences
- A bot's runs can come from its schedule, Preview now, a hand-over or the Trade page; the last two are told apart by their keys (`adopt-…`, `ticket-…`) and neither counts toward Max entries per day.
- `bot_entry`'s order-and-chase steps are one function used by scheduled, floating and Trade page entries.
- No migration.
