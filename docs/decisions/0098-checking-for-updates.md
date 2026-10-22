# 0098. Checking for updates from the menu

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
Updating meant finding the Releases page, downloading, unzipping and running the installer by hand. Friends who are not technical did not know when a new version was out. The app only ever talked to a user's broker and, if turned on, to Discord or Telegram. A check for updates is the first traffic that is neither.

## Decision
- Once a day the app asks GitHub's public releases API for the latest release of the OptionsJourney repository. Nothing is sent about the user: it is a plain request with no login, no account and no identifier. Like any request it carries a User-Agent that names the app and its version (`optionsjourney/0.2.0`).
- If a newer version is out, the OJ menu offers **Download vX**, then **Install vX…**. Installing opens an Install page that lists what is live (market open, armed bots, stops the app watches, bot orders working). Each box must be ticked, and a changed count must be ticked again.
- The installer runs in a visible window. If it fails it puts the old version back.
- Every data upgrade makes a backup first, named `…-before-<to>-from-<from>.db.gz`; the newest 3 are kept. An older version that finds newer data shows a page with **Restore the backup from before the upgrade**.
- A setting, **Check for new versions** in Settings → This computer, turns the check off. It is On by default. The Welcome step says so.
- `OJ_UPDATE_URL` points the check at another host, for testing before the repo is public. Downloads are accepted only from the host the check used.

## Why
- GitHub's public API needs no key or account of ours and no server to run. The Release is already what people download.
- Telling people what the restart interrupts matters here: bots and watched stops stop while the app is stopped.
- A backup and a way back make an update safe to try for someone who cannot fix a broken install.

## Consequences
- GitHub sees the user's IP address, as with any web request. This is said in the Welcome step.
- Publishing a Release is now what makes an update visible to everyone, so a release is not published until it is ready.
- The oldest version one can go back to is v0.2.0, the first with this backup.
