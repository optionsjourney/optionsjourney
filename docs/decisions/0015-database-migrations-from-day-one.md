# 0015. Database migrations from the first table

- **Status:** Accepted
- **Date:** 2026-10-09

## Context
Every friend has their own copy of the database on their own computer (see [0002](0002-everyone-runs-their-own-copy.md)). When a new version of the app changes a table, each of those databases has to be upgraded safely, without losing trades.

## Decision
- **Alembic owns the schema:** it manages the database schema from the very first table. Migrations live in `src/optionsjourney/db/migrations/`.
- **Upgrades are automatic:** the app upgrades its database to the latest version every time it starts.
- **A test catches forgotten migrations:** it compares the migrated database with the models, so a model change without a migration fails the test suite.

## Why
- **No one runs commands:** friends get schema changes simply by updating the app.
- **Starting now is cheap:** adding migrations later would mean guessing what shape each existing database is in.

## Consequences
- **Every model change needs a migration.** [developer/database.md](../developer/database.md) explains how to write one.
- **SQLite needs batch mode.** SQLite can't alter most things in place, so migrations run in Alembic's "batch" mode, which rebuilds the table.
