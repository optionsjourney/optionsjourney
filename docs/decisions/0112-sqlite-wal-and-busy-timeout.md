# 0112. SQLite runs in WAL mode with a 10-second busy timeout

- **Status:** Accepted
- **Date:** 2026-10-23
- **Builds on:** [0002](0002-everyone-runs-their-own-copy.md) (everyone runs their own copy, on SQLite)

## Context
The app, its background jobs (fill sync, auto-sync, bots, alerts, P/L Day snapshots) and its pages all share one SQLite file. On SQLite's defaults, a write blocks every reader until it finishes, and a second writer gives up with "database is locked" after 5 seconds. The more the app does in the background, the likelier a page stalls or a bot's write fails. This came up while weighing a once-a-minute price recorder for trade trends (#289, parked). Moving to PostgreSQL was considered and rejected again for the reason in 0002: friends would have to install and run a database server.

## Decision
- **WAL mode** (`PRAGMA journal_mode = WAL`) on every connection `make_engine` opens. Reads never wait on a write, and a write never waits on reads.
- **A 10-second busy timeout** (`PRAGMA busy_timeout`), so a writer that finds another writer mid-write waits its turn instead of failing.
- **No migration step.** WAL is stored in the file, so data from an older version switches the first time this version opens it. Older versions still open a WAL file, so going back a version works.

## Why
- It removes the common lock collisions without changing the database, the install or the backups.
- SQLite's online backup API, which backups and restores already use, copies WAL databases correctly, including changes not yet folded into the main file.

## Consequences
- Two more files sit beside the database while the app runs: `optionsjourney.db-wal` and `optionsjourney.db-shm`. Copying only `optionsjourney.db` while the app is open can miss recent changes. The guide says to quit the app before copying the data folder, or to use Backup.
- WAL needs the database on a local disk. A data folder in a cloud-synced or network folder (via `OPTIONSJOURNEY_DATA_DIR`) can break locking. The guide warns against it.
- Writes still happen one at a time. A long write can now delay another write by up to 10 seconds where before it failed after 5.
