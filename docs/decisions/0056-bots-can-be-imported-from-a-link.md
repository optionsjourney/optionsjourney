# 0056. Bots can be imported from a link

- **Status:** Accepted
- **Date:** 2026-10-15
- **Replaces:** the "sharing is a file, not a link" part of [0033](0033-bot-file-format-version-1-shared-as-a-file.md) (bots slice 8 spec, question 1). The file format and everything else in 0033 stand.

## Context
Slice 8 shared bots only as a file: Tracker2 imports by fetching a pasted share link, but its links live on its own public website, and this app only listens on this computer (0013), so it has none. The author wants to paste a link on **Import a bot** too. People already post files online (Google Drive, Dropbox, GitHub, Discord), so a link to a bot file posted there is something a friend can paste.

## Decision
- **Import a bot takes a link** to a bot file posted online, alongside choosing the file or pasting it. It's fetched once, on Preview; the preview carries the text on to Import, as for a file. Fetched text is read by the same strict `bot_file.parse`.
- **Share pages become downloads:** Google Drive, Dropbox, GitHub file pages and gists are turned into their file's own link (Tracker2's `normalize_url` idea, for the sites friends use).
- **Only the internet:** `https` only, never this computer or the home network: the link, every redirect and every address its name leads to are checked. A link can't reach a router's page, a printer or this app. This app's own **Download bot file** link (which only works on this computer) gets a message saying to download the file instead.
- **Limits** as Tracker2's: a 10-second timeout, at most the 256 KB file limit read.
- **The link is kept** on the imported bot (`imported_from_url`) and shown on its page, as Tracker2 does.
- **Export doesn't change:** there is still no public share page, as there's still no public address.

## Why
- Pasting a link is easier for friends than downloading then choosing a file, and it's what Tracker2 does.
- Checking every step keeps a pasted link from being used to reach the home network: the app runs on the user's own computer.

## Consequences
- Importing from a link is the first time Import reaches the internet; the trading guard passes it (a read, to another host).
- A file shared privately (Drive's "restricted", a Dropbox link needing sign-in) gives a web page instead of the file, and the message says to share it so anyone with the link can view it.
