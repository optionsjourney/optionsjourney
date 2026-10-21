# 0074. A deleted bot's tag goes onto its trades as their own

- **Status:** Accepted
- **Date:** 2026-10-18

## Context
Decision 0072 has a bot's trades carry the tag it files under, added each time the journal is shown. A bot with trades isn't removed when you delete it: it's hidden, so its trades keep their link to it. Its tag kept being added too, so a deleted bot's trades showed a tag that couldn't be taken off anywhere: the trade page's tags had no say over it, and the bot it came from was gone from Automation.

## Decision
- Deleting a bot first puts its tag on each of its trades (the ones it opened and the ones matched to it) as their own tag, like a tag you added.
- A deleted bot's tag is no longer added to its trades when the journal is shown. What's on them is their own, so it can be unticked on the trade page or removed from the select bar.
- The deleted bot still points at the tag, so the tag can't be deleted. The Tags page lists the bot as "(deleted)". Letting a bot point at no tag would mean rewriting the bots table, which isn't worth it for this.
- Bots deleted before this change don't get their tag copied: their trades keep only the tags already saved on them.

## Consequences
- Deleting a bot changes nothing you see on its trades, and from then on their tags are yours to edit.
- On a live bot's trades, the bot's tag stays ticked and locked on the trade page, with a link to the bot's Journal tag setting (the only place it changes).
