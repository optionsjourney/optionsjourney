# 0057. Tracker2 bot files import

- **Status:** Accepted
- **Date:** 2026-10-15

## Context
Import a bot read only this app's own file (`optionsjourney.bot`, decision 0033). The author's bots live in Tracker2, whose share files (`tracker2.bot`, schema 1, `share_format.build_snapshot`) and share links were turned away as "not an OptionsJourney bot file". This app's settings were ported from Tracker2's, mostly with the same names and meanings, so a Tracker2 bot can be brought over almost as it is.

## Decision
- **Import a bot reads a Tracker2 bot file**, and a Tracker2 share link (`…/share/bot/<token>` becomes its `.json`, as Tracker2's `normalize_url` does) through the same link checks as any link (decision 0056).
- **Translated, then read like our own:** `bots/tracker2_file.py` renames and reshapes a Tracker2 file into this app's file, and `bot_file.from_file` reads that as strictly as any file. Only the settings this app shares come in: never sizes, sizing, contract limits, the account, the alert name or tags. The bot arrives off and in preview, as every import does.
- **What doesn't exist here** is mapped to the nearest setting, and the preview lists each under **Coming from Tracker2**:
  - Chase endings *Keep conceding* and *Leave working and alert* become *give up* (left out in bots slice 19); *Ignore bid-less longs* isn't carried over.
  - A bot without leg groups becomes one group, *Whole trade*, holding every leg with the bot's stop, target and profit actions (slice 19: a one-group bot's watched stop is the whole entry's).
  - A group that isn't one sold leg stops on a % of its entry (`group_entry`), as the app watches its stop (decisions 0039, 0054).
  - A time action that moves the stop is read in each group's own stop style (slice 4); Tracker2 reads it as a % loss of the entry. They agree for a group of one sold leg.
  - Exit mode (other than hybrid) and an Analyzer entry source are noted; exit time in force and the poll interval are dropped silently (fixed here).
- **Which exit rests:** each group's `broker_exit`, or for a file without one, the target for a debit bot and the stop otherwise (as this app's own version 1 and 2 files); decision 0054 then makes any group that isn't one sold leg rest its target.
- A Tracker2 schema newer than 1 is refused with the reason.

## Why
- The author's working bots are in Tracker2; re-typing them invites mistakes.
- Translating into our own format keeps one strict reader, so a Tracker2 file can't set anything our own file can't.

## Consequences
- Tracker2 changes to its share format need a new schema here; a newer schema is refused until then.
- Values Tracker2 allows but this app doesn't (more chase tries than 20, say) show on the preview as the bot's validation problems, and nothing is imported until the file is fixed.
