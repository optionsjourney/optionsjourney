# 0005. Bots are shared as JSON files

- **Status:** Accepted (format still to be designed)
- **Date:** 2026-10-09

## Context
The author has built and tuned trading bots in Tracker2. Friends want to use them without building their own from scratch.

## Decision
A bot is described by a **JSON file**. It records the strategy's settings: underlying, structure, entry times, width, stops, targets and so on. The file **never** contains account numbers, credentials or position sizes.

The author publishes bots to a **bot library**, either on `tracker.optionsjourney.com` or in a folder in this repository. In the app, a friend browses the library, adds a bot, picks which of their accounts it trades in, and sets their own size.

## Why
- **Readable and checkable:** a JSON file is plain text, so anyone can see exactly what a bot will do before turning it on.
- **Easy to version:** a bot can be published as a new version without affecting copies already in use.
- **Personal details stay out:** account details and sizing are set by each user, so a shared file can't touch anyone's money by itself.

## Consequences
- **The format needs designing.** We'll design it from how Tracker2's bot engine defines bots, and it needs a version number so old files keep working.
- **The app must validate every downloaded file** and reject anything it doesn't understand.
- **Users must not get silent changes.** When the author publishes an update to a bot, users should see what changed and choose whether to take it.
