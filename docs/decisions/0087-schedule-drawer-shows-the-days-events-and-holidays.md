# 0087. The schedule drawer shows the day's economic events and holidays

- **Status:** Accepted
- **Date:** 2026-10-20
- **Builds on:** [0079](0079-schedule-drawer.md), which left the economic events out.

## Context
Decision 0079 moved today's schedule into Tracker2's drawer but didn't take the economic events Tracker2 shows at its top. The app already reads them (decision 0032), but only the bots' date lists used them, so you couldn't see on the schedule that CPI was out at 08:30. A closed day said only "Market closed", without saying which holiday. Issue #221.

## Decision
- The drawer shows the day's **economic events** above the entries, as Tracker2's chips: high impact first in red with 🟥, medium in amber, each with its New York time. A list's release (FOMC, CPI) is one chip; ForexFactory's rows win over the Fed's or FRED's. Hovering shows each release with its forecast and previous figure.
- ForexFactory's **forecast** and **previous** are now stored with each event (migration 0048).
- A closed day **names the holiday** from NYSE's calendar ("Market closed: Thanksgiving Day"). A day past the holiday calendar says it's treated as a trading day. The early-close note was already there.
- **Not adopted:** Tracker2's chips on the journal's date headers.

## Why
- It's what Tracker2 shows, and the author reads the day's events there before the open.
- Only events already read for the date lists are shown, so nothing new is fetched. Beyond this week only FOMC and CPI days show, as in Tracker2.

## Consequences
- Testing this turned up that NYSE's page no longer carried its early closes where the parser looked, so the calendar refresh failed and no holiday names were stored. That's fixed in #222.
- If the events can't be read, the drawer just leaves them out; the schedule still shows.
