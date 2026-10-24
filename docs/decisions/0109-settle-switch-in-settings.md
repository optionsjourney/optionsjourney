# 0109. The Settle expirations switch moves to Settings

- **Status:** Accepted
- **Date:** 2026-10-23
- **Amends:** where [0062](0062-settle-expirations-at-the-close.md) put the switch. The rest of 0062 (and 0078) stands.

## Context
**Settle expirations at 4:15 PM** sat above the Journal's trade list, with a note under it from the last run: what it left for the broker and why ("its account hasn't synced since the close; tried again until 8 PM"). The switch is set once and rarely changed, which makes it a setting. The note stayed up all the next day and said nothing the trade rows don't already show: a trade left for the broker shows **Expired, waiting on broker**.

## Decision
- The switch is a setting in **Settings → Syncing your trades** (key `expiry_settle_on`, the same row and `1`/`0` values the switch stored, so nothing is lost). Off by default, as before.
- The Journal shows neither the switch nor the last-run note. The last run is still recorded (`expiry_settle.last_run`) and used by the job itself.

## Why
- A rarely changed preference belongs with the other sync settings, not above the trades you look at every day.
- The note repeated what each trade's status already says, and stayed up after it stopped mattering.

## Consequences
- `POST /journal/settle-expirations` is gone; the setting saves with the rest of the section.
