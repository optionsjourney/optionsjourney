# 0104. Open from other devices is a switch in Settings

- **Status:** Accepted
- **Date:** 2026-10-22
- **Amends:** [0063](0063-optional-local-network-access.md)

## Context
[0063](0063-optional-local-network-access.md) lets the app answer on the network when `.env` has `LISTEN_ON_NETWORK=yes`. That works for someone running from a git checkout, but not for people who used the installer:
- **Updates lose it.** Both installers move the old app folder aside and make a fresh copy, so a `.env` added to the app folder is gone after the next update, and the app quietly goes back to this computer only.
- **It's hard to make.** On a Mac, a file whose name starts with a dot is hidden in Finder, and editing one is beyond most non-programmers.

The site's new FAQ answers "Can I use it from my other computers or my phone?", so turning it on needs to be something anyone can do.

## Decision
- **A switch in Settings → This computer: Open from other devices**, Off by default. It's saved with the user's data (the settings table), so updates and reinstalls keep it.
- **On, the app listens on all network interfaces** (`0.0.0.0`), as `LISTEN_ON_NETWORK=yes` does. It takes effect after **Restart OptionsJourney**.
- **`.env` still works.** `LISTEN_ON_NETWORK=yes` opens it whatever the switch says.
- **Demo mode ignores the switch**, so a visitor can't open a demo seat to the network from its Settings page.
- **One place decides:** `services.settings.bind_host(settings, session_factory)`. Both the menu bar app and `uv run optionsjourney` use it. `Settings.bind_host` is now only what `.env` asks for.

## Why
- **Kept across updates,** because it lives with the user's data, not in the app folder.
- **No file to edit,** and the switch's ⓘ says plainly what it exposes.
- **Off by default** keeps 0063's safe default for everyone who never touches it.

## Consequences
- **Still no login.** With the switch on, anyone who can reach the computer can see the accounts and place orders. The switch's tip and the FAQ say so, and point to Tailscale for phone access. They also say that an address on the public internet must have a login service, such as Cloudflare Access, in front of the app.
- The startup problem page (shown when the app can't start) still follows `.env` only: it opens before the saved settings can be read.
- The trading guard, preview/live and Stop all bots checks are unchanged, as in 0063.
