# 0002. Everyone runs their own copy, on SQLite

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
There were three ways to give the app to friends:
1. One website the author hosts, where everyone logs in.
2. Each friend runs their own copy on their own computer.
3. Each friend gets a small cloud server.

## Decision
**Each friend runs their own copy on their own computer.** It stores data in **SQLite**, a single database file on that computer, instead of the PostgreSQL server Tracker2 uses.

## Why
- **Trust:** friends' broker logins and trade history never leave their own computer. The author never holds anyone's credentials or data.
- **No hosting:** there's no server to run, pay for, secure or keep online.
- **No logins needed:** since each copy has one user, there's no account system to build.
- **SQLite needs nothing installed:** PostgreSQL would mean installing and running a database server, which is too much to ask of non-technical users.

## Consequences
- **Bots stop when the computer is off or asleep.** The app has to keep the computer awake during market hours while bots are active (see [0008](0008-menu-bar-launcher.md)).
- **Each friend connects their own broker API access,** which needs a guided setup (see [0007](0007-guided-setup-wizard.md)).
- **Updates have to reach each copy.** We'll need a simple way to update.
- **Code from Tracker2 that relies on PostgreSQL-specific features has to change** as it's brought over.
