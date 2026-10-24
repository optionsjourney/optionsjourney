# 0105. The bell opens a list of recent alerts, and its number counts new things and problems

- **Status:** Accepted
- **Date:** 2026-10-23

## Context
The top bar's bell went straight to the Alerts page, and its red number counted everything the page listed as needing attention: problems, notes such as "Schwab last synced 3 days ago", and price or trade alerts that had gone off. Reading those didn't lower the number. It only went down when the cause was fixed, or when the user pressed Dismiss or Set again. People read a number that stays up after they've looked as a bug.

## Decision
- **Clicking the bell opens a "Recent alerts" list under it**, as Tracker2's bell does. The list shows what needs fixing or a look first (problems, then notes), then the 15 newest alerts, with the new ones highlighted, and an **All alerts →** link at the bottom. Cmd/ctrl-click still opens the page. The list comes from `GET /api/alerts/recent`.
- **The number counts new alerts and problems.** A new alert counts until it has been seen, in the list or on the Alerts page. A problem (an account that didn't sync, an order whose result is unclear, a failed backup, an expired Schwab login) counts until it's fixed. Notes (a stale sync, a Schwab login running out soon) are listed but don't count.
- **A price or trade alert that goes off is also stored as an app alert** (kind `user_alert`, not sent again), so it counts as new until seen. It stays on the Alerts page's list with Set again and Dismiss until the user does one of those.
- The Alerts page's history heading is **Recent** (it said "Unread", even for alerts already read).

## Why
- People look at the bell to see what's new. A number that drops once they've looked tells them nothing is waiting.
- A real problem can cost money if it's missed, so it keeps the number up until it's fixed. A note doesn't need that.

## Consequences
- A dismissed price alert still appears in the Recent history.
- The badge's live update (`alerts_changed`) adds the problem count from page load to the unread count, as before (`data-problem-count`).
