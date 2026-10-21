# 0023. Manual entry, backups and account value history

- **Status:** Accepted
- **Date:** 2026-10-10

## Context
[0021](0021-product-scope.md) left three questions open: what manual trade entry covers, where backups go, and how account values over time work when Schwab has no balance history. Research for the last one (#7) found that TastyTrade returns a daily end-of-day snapshot per account (`/accounts/{account}/balance-snapshots`), while Schwab only gives the current balance. Tracker2's own code says the same.

## Decision
**Manual trade entry** (#8) is for trades the app can't sync: a broker it doesn't connect, or a user with no broker connected.
- The user enters the date and time, a "manual" account they name, the underlying, and each leg (call, put or shares; strike; expiration; quantity; buy or sell), plus the fill price and fees. Closing works the same way, or the trade is marked expired.
- Manual trades go through the same pipeline as synced ones (orders, then positions, then P/L), so they appear everywhere synced trades do.
- **They're not for patching synced accounts.** There the broker is the source of truth, and a missing fill is a sync bug. A hand-entered copy would double-count once the broker's record arrives.
- Edit and delete apply only to manual trades.

**Backups** (#13):
- **Where:** a folder the user picks in Settings, by default `Documents/OptionsJourney Backups`. A folder in iCloud Drive, Dropbox, OneDrive or Google Drive gives off-computer backups for free.
- **How often:** daily (the default), weekly or off, plus **Back up now**. The last 30 backups are kept.
- **How:** SQLite's backup feature, which is safe while the app runs. One dated file per backup.
- **Restore:** from Settings. The current database is backed up first, so a restore can be undone.
- **Missed backups:** a backup missed while the app was off runs at the next start. If the folder can't be reached, an alert says so.

**Account values over time** (#7):
- **TastyTrade:** the user is asked how far back the history should start, and that history is filled in from TastyTrade's daily snapshots. New days are added on each sync.
- **Schwab:** the app explains that Schwab doesn't provide history, so values are recorded once a day, from the day the app starts doing so, and days the app is off stay empty.
- **Deposits and withdrawals** come from the synced transactions, not from manual entry.

## Why
- **Manual entry:** covering only unsynced trades keeps one source of truth per account, and the shared pipeline means no second set of P/L rules.
- **Backups:** a user-chosen folder reuses whatever cloud drive the user already has, with no accounts or services of our own. SQLite's backup feature can't catch the file half-written.
- **Account values:** asking TastyTrade users where to start avoids guessing how much history they want. Telling Schwab users plainly why theirs is missing is better than a chart that silently starts late.

## Consequences
- **The setup wizard and Settings** ([0007](0007-guided-setup-wizard.md), Phase 3–4) need a "history start date" question for TastyTrade, and a short Schwab explanation.
- **Manual accounts** are a new kind of account: no broker and no sync. They need to fit the accounts page and the account filter.
- **Backups need** a scheduler that runs while the app is open, and a check at start-up.
