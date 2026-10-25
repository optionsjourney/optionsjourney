# 0120. Only brokers you're logged in to are asked

- **Status:** Accepted
- **Date:** 2026-10-25

Issue #304.

## Context
The app always made both broker clients, and every feature (Pending, Stocks, Accounts, sync, quotes, charts…) called both. With Schwab never connected, each call raised "Schwab isn't connected" and each feature handled it its own way. Pending logged an error with a full traceback on every refresh. Settings already knew Schwab wasn't connected. Tracker2 doesn't check either: it calls and fails.

## Decision
- **`broker_clients()` lists only the brokers you're logged in to** (a stored refresh token). It's decided in one place, so no feature asks a broker you never connected.
- **An expired login still counts.** Schwab after 7 days stays in the list, so its "log in again" problem still shows where it did.
- **The quote hub keeps both clients,** so a broker you connect later streams without a restart.
- **Gateway mode is unchanged:** its credentials have no such check, so every client is used.

## Why
- Asking a broker the app knows isn't connected only fills the log and slows pages. Settings is where you connect a broker and see whether it is.

## Trade-off
- A broker you never connected no longer appears on the Accounts page as a "isn't connected" card. Neither does one you disconnected, along with the balances last read from it.
