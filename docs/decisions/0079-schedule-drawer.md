# 0079. Today's schedule as Tracker2's drawer

- **Status:** Accepted
- **Date:** 2026-10-20
- **Replaces:** the "where the schedule lives" default in the bots slice 20 spec (`docs/superpowers/specs/2026-10-14-bots-slice-20-seeing-what-bots-do-design.md`, question 3): a fold-out on the Automation page plus a page for other days.

## Context
Slice 20 showed the day's schedule as a fold-out table on the Automation page, with a separate `/automation/schedule?date=` page for other days. Tracker2 shows the same thing in a drawer (`_schedule_drawer.html`, `schedule_drawer.js`). It opens from a Schedule button, and you step from day to day without leaving the page. Each entry is a compact row with the bot's account and tags. Issue #195.

## Decision
- A **Schedule** button in the Automation page's header opens a drawer from the right. Like Tracker2's, it has ‹ › to step a weekday at a time, a date box, Today, count badges, and "✓ Every entry below has a live scheduler job" when the schedule check passed and something is still to come.
- Each entry is a Tracker2 row: a coloured dot for its state, the time, **Skip day**, the bot's name with Live or Paper, "SPX · 1 contract", the outcome (linked to the run), and the account and tag chips.
- **Skip day skips the day shown**, today or any later day (Tracker2's rule). Before, it skipped only today. An **Undo** appears in the drawer. Past days can't skip.
- The fold-out and the separate page are removed. `GET /automation/schedule?date=` now answers JSON for the drawer, and `POST`/`DELETE /automation/<id>/skip-day?date=` skips or un-skips one day.
- Not yet adopted: the drawer on the journal page, and the day's economic events at the top.

## Why
- One place to see any day, without leaving the Automation page. It matches Tracker2, which the author already uses every day.
- The rows show at a glance which account and tag each entry belongs to.

## Consequences
- The schedule needs JavaScript (as the journal's drawers already do).
- The day plan's rows now carry the underlying, the account and the tags. Its notes say which ones are warnings.
