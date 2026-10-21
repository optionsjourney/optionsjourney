# 0083. Blackout lists checked on the entry day or before expiration, and a Market holidays list

- **Status:** Accepted
- **Date:** 2026-10-20

## Context
A bot's blackout lists (FOMC days, CPI days, last trading day of the month) were checked on the entry day only. A trade that lasts several days can still be open over the event, which is often what you want to avoid. Tracker2 first added one switch to check every day up to expiration (commit 7dd68099), then replaced it with two checks per list (ef862660, `bots.blackout_dte_sets`), and added a Market holiday list for trades held over a market closure (04eaa8e4). Issue #207, part of #197.

## Decision
- Each blackout list has two boxes, as in Tracker2's final shape:
  - **Entry day** (`blackout_date_sets`, as before): skip when the list's day is the entry day.
  - **Before expiration** (`blackout_dte_sets`, new, migration 0046): skip when the list's day falls after the entry day, up to and including the expiry estimate. An entry on the event day itself still trades unless Entry day is ticked too.
- The **expiry estimate** is Tracker2's: the entry day plus the longest leg's DTE in calendar days, moved on to the next session. A 0DTE bot has no days after the entry, so Before expiration never skips it.
- A new list, **HOLIDAY** (Market holidays): weekdays NYSE is shut all day, from the same calendar as end of month (a half day isn't one). It is Before expiration only: refused as a specific date or an Entry day list. A Tracker2 file that has it as an entry-day list (its earlier form) imports it as Before expiration, as Tracker2 reads it.
- A skipped run names the day: "today is a blackout day (FOMC on 2026-10-28, before expiration)". The bot page's summary reads like Tracker2's: "FOMC (entry day + before expiration), Market holiday (before expiration)".
- The bot file and the Tracker2 import carry `blackout_dte_sets`. A file without it has none.
- **One change from Tracker2:** if a Before expiration list can't tell about a day before expiration, the entry is skipped. Tracker2 checks only the entry day for that and treats an unknown later day as no event. This app already fails closed when a list can't tell about the entry day (bots slice 5), so the window follows the same rule.

## Why
- Following Tracker2's final shape (two checks per list), not its first switch, lets a bot avoid holding over FOMC while still trading on FOMC day itself, which one switch couldn't express.
- Failing closed matches what the guide already promises: a blackout list that can't tell skips the entry, because the day might be one you asked to avoid. The usual case is a stale Fed or FRED read, which already makes today unknown, or a long trade reaching past the last year of the holiday calendar.
- Turned down: a separate "within DTE" switch (Tracker2's intermediate form, replaced within the hour), and looking up the real expiration from the option chain (the guard runs before strikes are picked; Tracker2 uses the estimate too).

## Consequences
- Existing bots are unchanged: migration 0046 gives every bot an empty Before expiration list.
- A bot that ticks Before expiration looks up each list for every day in its window at each entry (one database session), and the schedule drawer does the same over the armed bots' windows.
- A bot with a long DTE that ticks Market holidays may skip entries near the end of the last year the holiday calendar covers, until NYSE publishes the next year. The alert and the skip reason say which list can't tell.
