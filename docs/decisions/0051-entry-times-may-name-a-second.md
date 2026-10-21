# 0051. Entry times may name a second, and the Schedule shows only what's used

- **Status:** Accepted
- **Date:** 2026-10-15

## Context
The bot form's Schedule showed every field at once: the entry window next to the entry times it replaces, the fields for all four frequencies, and the blackout dates while blackout days was off, each explained in a paragraph. Entry times had to be typed as `09:47` exactly, there were always three blank rows, and the weekdays included Saturday and Sunday. Tracker2's form shows only what the choices use, reads `945` as 09:45, adds and removes rows by button, and lets an entry time name a second on a 15-second step.

## Decision
- **Only what's used shows** (Tracker2's applyEntryToggles): the window's times or the entry times, the chosen frequency's fields, the blackout dates while ticked, and an entry time's contracts only when the bot sizes by contracts. Hidden fields are still sent, so nothing typed is lost. Without JavaScript, every field shows.
- **Times are read like Tracker2's normalize_time**, on the server so it holds with or without JavaScript: `945`, `0945` and `9:45` are 09:45, and three digits are padded on the left (`130` is 01:30). The entry window's times use the same reader.
- **An entry time may name a second, in 15-second steps:** `9:30:15` or `93015` is stored as `09:30:15`. Zero seconds are dropped, so `09:30` and `09:30:00` can't both be on a bot. The window stays in whole minutes, as in Tracker2.
- **An entry time has no Label box** (Tracker2's form has none; the label showed only on the import page). A label already saved, or brought in by a bot file, is kept and still shared.
- **Weekdays are Monday to Friday**, shown as toggle buttons. They are still checkboxes, sent as before.
- **No migration.** The time columns stay `VARCHAR(5)`: SQLite never enforces the length, so `09:30:15` fits. Widening them would mean rebuilding `bot_runs`, and with foreign keys on, dropping the old table would cascade-delete the rows that point at it.

## Why
- One way of entering at a time, and only the fields for the choices made, is easier to read. It is also what Tracker2 does.
- An entry just after the open (09:30:15) is a real use, and the 15-second step matches Tracker2.
- Reading the time on the server, not only in the page, means a bot file or a form sent without the script is read the same way.

## Consequences
- The app's clock checks every 10 seconds, so a time with seconds enters within about 10 seconds of it (Tracker2's scheduler fires on the second).
- A weekly bot that had Saturday or Sunday ticked loses those days the next time it is saved. They never ran, because the market is shut.
- If the app ever moves off SQLite, the two time columns need widening to 8 characters first.
