# 0033. The bot file format, version 1, shared as a file

- **Status:** Accepted
- **Date:** 2026-10-13
- **Builds on:** [0005](0005-shareable-bots-as-json.md) (bots are shared as JSON files)

## Context
0005 decided that bots are shared as JSON files, but left the format to be designed. Tracker2 shares a bot through a public link that serves a page and the JSON, and imports one by fetching a pasted link. This app only listens on this computer (0013), so it has no public address to share from. A shared file also comes from someone else, so the app has to be strict about what it accepts.

## Decision
- **Format:** `{"format": "optionsjourney.bot", "version": 1, "exported_at": …, "bot": {…}}`.
  - Money is written as text.
  - Legs and sides refer to each other by position.
  - An allowlist decides which fields go out. Every bot field is classed as shared or private, and a test fails on a new one until it is classed.
  - A file never holds an account, credentials, contract sizes or limits, the strategy, tags, or anything about runs.
- **Versions:** a newer version than the app knows is refused with "Update the app". Version 1 files keep working.
- **Sharing by file:** you download the file and send it yourself. The friend imports it by upload or paste. There is no hosted library and no share link, and the app fetches nothing from the internet.
- **Unknown keys are ignored,** as in Tracker2, so a file can't set a private field such as an account or "live".
  - The import preview shows a summary of the bot that will be saved, and any problems, before anything is written.
  - The importer then picks the name, strategy, account and sizes.
  - The bot is saved switched off and in preview, after the same validation as the bot form, which also bounds what only a file could set (SPX only, an iron condor's 4 legs and 2 sides, chase limits).

## Why
- A file works with no server and no public address, and it can be read before importing it.
- Ignoring unknown keys lets a file from a slightly different build still import, without letting it reach anything private.
- Turned down:
  - **Tracker2's hosted link:** it needs a public hostname.
  - **Fetching a pasted link:** it would be the only place the app requests an address a user typed.
  - **Refusing any unknown key:** it would be stricter, but it would reject harmless extras and protect nothing more, since unknown keys are never read.

## Consequences
- A bot library, and "what changed" when a shared bot is updated (#34), need a place to publish files. That's left for later; `imported_at` records which file a bot came from.
- Any new bot setting needs a choice: shared or private. Sharing one means adding it to the format, as a new version if old apps would misread it.
