# 0049. The bot tag picker offers the journal's tags too

- **Status:** Accepted
- **Date:** 2026-10-15
- **Builds on:** [0025](0025-strategy-tags-and-stocks.md) (every strategy has a tag)

## Context
The bot form's tag picker (#109) followed Tracker2's `_tag_catalogue`: it offered only tags already on a bot. A first bot has none to offer, so its **Pick tags** menu was empty, while the journal already holds tags (one for each strategy, and the user's own).

## Decision
- **Pick tags** offers every tag on a bot that isn't deleted *and* every journal tag, a bot's spelling winning when they differ only in capitals. The state words (Armed, Off, Live, Preview) are never offered.
- Picking a journal tag only labels the bot, as any bot tag does. It doesn't tag the bot's trades, and it doesn't add a journal tag; a new tag typed on the bot form stays a bot tag.
- The menu always shows, with "No tags yet" when there are none.

## Why
- One list of names to pick from, so a bot can be filed under the same words as its trades without retyping them.
- Keeping the two kinds of tag separate means the bot list's filters and the journal's filters don't change.

## Consequences
- A journal tag renamed or deleted later leaves bots that picked it with the old name.
- Tagging a bot's trades from the bot (the other option offered for #109) is still open.
