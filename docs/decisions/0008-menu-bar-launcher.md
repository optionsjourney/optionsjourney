# 0008. Runs as a menu bar / tray app

- **Status:** Proposed
- **Date:** 2026-10-09

## Context
Tracker2 is started with `python run.py`, and the terminal window has to stay open. Non-technical users will close that window and their bots will stop, usually without them noticing.

## Decision
OptionsJourney runs as a small **menu bar app** on Mac, or a **system tray app** on Windows:

```
  OptionsJourney ▾
  ─────────────────────
  ● Running · 2 bots active
  ⚠ Schwab needs reconnect
  ─────────────────────
  Open OptionsJourney
  Stop all bots
  Start at login        ✓
  ─────────────────────
  Quit
```

- **Starting it:** double-click an app icon. There's no terminal, and the app runs quietly in the background.
- **Starts at login,** so bots are ready every market morning.
- **Keeps the computer awake** during market hours while bots are active.
- **Warns before quitting** while bots are live.
- **Packaged with Python inside:** later, the app becomes a real `OptionsJourney.app` (and a Windows `.exe`), so nobody installs Python themselves.
- **Developers can skip it:** `python run.py` still works.

## Why
- **It's what people already know:** this is how normal background apps behave (Dropbox, Zoom and others).
- **Problems stay visible:** a status icon shows trouble, such as a broker needing to reconnect, without opening anything.

## Consequences
- **Apple's security warning:** without a paid Apple Developer account ($99/year) to sign the app, Mac shows a "can't be opened" warning the first time. Right-click → Open gets past it, and the guide will show how. Signing is worth it if many people use the app. Windows shows a milder SmartScreen warning.
- **Testing on clean machines:** packaging has to be tested on a computer that has never had Python or developer tools installed.
