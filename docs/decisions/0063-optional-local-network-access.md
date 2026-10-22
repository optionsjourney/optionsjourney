# 0063. The app can optionally answer on the local network

- **Status:** Accepted, amended by [0104](0104-open-from-other-devices-is-a-settings-switch.md)
- **Date:** 2026-10-16
- **Replaces:** [0013](0013-local-only-web-server.md)

## Context
[0013](0013-local-only-web-server.md) made the server listen only on `127.0.0.1`, with no setting to change that. In practice the author wants to open the app from other devices on the home network (another computer, a phone).

## Decision
A `LISTEN_ON_NETWORK` setting in `.env`. When it is `yes`, the server listens on all network interfaces (`0.0.0.0`), so any device on the same network can open `http://<this computer's address>:<PORT>`. When it is missing or `no`, the app keeps listening only on `127.0.0.1`, as before. Both `uv run optionsjourney` and the menu bar app follow it.

## Why
- **Opt-in keeps the safe default.** Friends who never edit `.env` keep a this-computer-only app.
- **One setting, in one place.** `Settings.bind_host` decides the address; nothing else picks it.

## Consequences
- **No login.** With the setting on, anyone on the same Wi-Fi can see balances and use the app, including placing orders. Only turn it on for a network you trust. A login screen is still not built.
- The trading guard, preview/live and Stop all bots checks are unchanged; they apply the same way to requests from any device.
