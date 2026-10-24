# 0110. One set of tags: a bot's tags are journal tags, all on its trades

- **Status:** Accepted
- **Date:** 2026-10-23
- **Amends:** [0072](0072-tags-only.md) (a bot files under one tag) and the bot labels of bots slice 8 (#109, decision 0049).

## Context
A bot had two tag fields. **Journal tag** (`tags.id` in `bots.tag_id`) went on its trades and decided its Record. **Tags** (`bots.tags`, a list of names) were labels for finding bots on the Automation list and never reached a trade. They used the same words: MEIC - Beta had Journal tag `MEIC` and labels `0DTE, MEIC`, so it looked tagged 0DTE, yet a Journal view filtered by 0DTE didn't show its trades. The user's ruling: there should only be one set of tags.

## Decision
- **A bot's tags are journal tags.** `bots.tags` holds all of them, the main one first; every name is a row in `tags` (made when the bot is saved or bulk-tagged, migration 0053 for existing bots).
- **All of them go on its trades.** The journal adds them to a trade the bot placed or is matched to as it shows it, as it already did the main tag, so changing a bot's tags changes its past trades too. Tags the user put on a trade stay.
- **The main tag (`tag_id`) keeps its job:** the Record also counts unlinked trades with it in the bot's account (0072), the list groups bots under it, and it can't be deleted while the bot exists. It stays main while it's among the bot's tags; when it's taken off, the first other tag given takes over. A bot needs at least one tag.
- **The bot form has one Tags box**, with "Main tag: …" under it. The import page still picks one tag, which becomes the bot's only tag.
- **Renaming a tag renames it on bots; deleting one (not a main tag) takes it off them.**
- The Automation list filters by the same tags. State words (Armed, Off, Live, Paper) still can't be tags.

## Why
- One list means what you see on a bot is what its trades get, so a Journal view filtered by any of a bot's tags finds its trades.
- Keeping `tag_id` as the main tag leaves the Record, grouping and matching rules of 0072 unchanged.

## Consequences
- Labels that were only on bots (e.g. "Options") become journal tags and appear on those bots' trades.
- Bulk tagging a bot now tags its trades.
