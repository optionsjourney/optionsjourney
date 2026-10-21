# 0013. The app only listens on this computer

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
OptionsJourney shows account balances and will place orders, and it has no login screen (see [0002](0002-everyone-runs-their-own-copy.md)). If it listened on the network, anyone on the same Wi-Fi could open it.

## Decision
The web server always binds to `127.0.0.1`, which means this computer only. There is deliberately no setting to change that.

## Why
- **No login means no network access.** Without a login screen, being reachable only from this computer is the simplest protection there is.

## Consequences
- **No phone or other-computer access.** Using the app from a phone or another computer would need a deliberate, separate design with a login, and isn't planned.
