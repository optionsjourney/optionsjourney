# Database

SQLite, through plain SQLAlchemy 2 ([0012](../decisions/0012-broker-apis-over-plain-http.md)). The file lives in the user's data folder, never in the repo (see [getting-started.md](getting-started.md)).

## Tables (`db/models.py`)

| Table | What it holds |
| --- | --- |
| `accounts` | Each broker account seen during a sync: broker, number, nickname, type |
| `broker_transactions` | Every broker transaction exactly as received, as JSON ([0014](../decisions/0014-store-broker-transactions-raw.md)) |
| `sync_state` | Per account: how far it has synced, and the last error |
| `tags` | The labels trades are filed under; a trade can have any number (decision [0072](../decisions/0072-tags-only.md): there are no strategies). The starter tags come from migration 0002's starter strategies (0DTE, Credit Spreads, Iron Condors, Calendars, Wheel) through 0009 and 0043, plus Stocks (0008). Names are unique ignoring case and can't hold a comma. A bot files its trades under one (`bots.tag_id`, ON DELETE RESTRICT), so that tag can't be deleted while the bot exists |
| `bot_trade_matches` | A past trade matched to a bot by hand (migration 0042, #169, decision 0071): `position_key` (primary key; a condor split on match is linked by its whole key and its halves resolve through `split_from`), `bot_id` (ON DELETE CASCADE), `matched_at`. Read by `journal.matched_links` into `JournalEntry.bot` (`BotLink.matched`, no `run_id`) |
| `bot_auto_skips` | A trade a sync never matches to a bot on its own again (migration 0052, #276, decision 0108): `position_key` (primary key), `skipped_at`. Written by `bot_match.unlink` and `bot_match.undo`; read by `bot_match.auto_match` |
| `trade_events` | A journal trade opening, rolling, partly closing or closing (migration 0044, #178, decision 0077): `kind` (CHECK: open, roll, partial_close, close), `dedupe_key` (unique), `position_key`, the trade's `name` and `bot_name` then, `broker`, `account_number`, `amount` (+ received, − paid), `occurred_at`, `detail` (migration 0047, #212: a close's "Strikes: …" line and a "Rolled: …" line per roll, empty for other kinds). Written by `services/trade_events.record` after a sync; the Alerts page lists them and the popups show the new ones |
| `trade_edits` | The user's changes to one position, by position key: name, notes, close reason, iron condor split. A null means "the default" |
| `trade_tags` | Which tags each position has |
| `dashboards` | Saved journal views: a name, an order, and the filters as query arguments (`tag` holds tag ids, or `none` for "No tags"; migration 0043 turned `strategy` filters into their tags) |
| `settings` | Values saved on the Settings page, by key. What can be set, and the defaults, are in `core/settings.py` |
| `account_values` | Each account's value at the end of each day, from TastyTrade's history (`source` "history") or a Schwab balance read that day ("daily"). Amounts use the `Money` column type: exact decimals stored as text ([0023](../decisions/0023-manual-entry-backups-account-values.md)) |
| `latest_balances` | Each account's balance as last read (net liquidating value, cash, buying power, name, `read_at`), so the Accounts page can show it before the brokers answer. Overwritten by each read; accounts a broker no longer lists are removed ([0029](../decisions/0029-accounts-page-shows-stored-balances-first.md)) |
| `user_alerts` | Alerts the user set (price, trade P/L, or a spread's price with its `legs`; a combined trade alert keeps its trades' keys in `legs`): what to watch, above or below what level, and when it went off |
| `chart_layouts` | Chart grid layouts: a name, Tracker2's layout `payload` (shape and panes) and which one is active ([developer/charts.md](charts.md)) |
| `chart_tabs` | The grid's open tabs: a layout and each pane's symbol. Deleted with their layout |
| `chart_drawings` | Lines drawn on charts, by symbol and timeframe |
| `watchlist_groups` | The chart grid watchlist's groups and their symbols |
| `expiry_settlements` | Options the app settled at the underlying's close on expiration day (migration 0038, [0062](../decisions/0062-settle-expirations-at-the-close.md)): one row per account, contract and direction, with the close used, quantity and fee. Folded into positions until the broker's own record arrives ([positions.md](positions.md)) |
| `manual_bots` | Manual bots (migration 0040, #158): named exit rules to attach to any open trade. A profit target and stop loss (each a value with unit `pct` or `usd`, blank is off), an exit time (`close_time` HH:MM New York, null is off; `close_dte` days before expiration) and `alerts` (a bot's default alert rules). Deleting sets `deleted_at`, so run history keeps the name. Attaching copies the rules into a hidden bot with `bots.per_trade` true and `bots.manual_bot_id` set (null if the row ever goes: the database has ON DELETE SET NULL, not declared in the model because SQLite can't report it back for a column added in place) |
| `bots`, `bot_*` | The bots, their runs and exits. Described with each migration (0010, 0015–0027) in [developer/bots.md](bots.md#tables) |

Foreign keys are switched on for every SQLite connection (`make_engine`), so deleting a tag removes it from trades, and a tag a bot files under can't be deleted (`journal.delete_tag` checks first and says which bot).

Every connection also runs in **WAL mode** with a **10-second busy timeout** (`BUSY_TIMEOUT_MS`, decision [0112](../decisions/0112-sqlite-wal-and-busy-timeout.md)): reads never wait on a write, and a second writer waits its turn instead of raising "database is locked". WAL is stored in the file, so older data switches when first opened. Recent changes live in `optionsjourney.db-wal` until SQLite folds them in, so measure or copy the database through SQLite (the backup API, `PRAGMA page_count`), never the `.db` file alone.

**Migration 0043 (tags only, decision 0072):** every strategy becomes a tag of the same name (an existing tag, any case, is reused), every `trade_edits.strategy_id` becomes a `trade_tags` row, each bot gets `tag_id` from its strategy's tag, saved dashboards' `strategy` filters become `tag` filters (`none` stays `none`, now "No tags"), and `trade_edits.strategy_id`, `bots.strategy_id` and `strategies` are dropped. `bots` is changed **in place**, never copied (with foreign keys on, dropping the old table would cascade through `bot_runs` and the rest): `tag_id` is added with `ALTER TABLE ... ADD COLUMN`, and the table's `CREATE TABLE` text is edited through `PRAGMA writable_schema` (SQLite's documented way to change constraints the rows already meet) to drop the strategy foreign key and make `tag_id` NOT NULL with a table-level foreign key, before `ALTER TABLE ... DROP COLUMN strategy_id`. `trade_edits` has no dependants, so a batch copy is used there. The downgrade rebuilds strategies from the bots' tags only; trades keep their tags and get no strategy.

**Tags in the journal (`services/journal.py`):** `load_journal` gives each `JournalEntry.tags` the trade's `trade_tags` (an unedited split half takes its whole trade's), plus **Stocks** on a stock trade and, on a trade a bot placed or was matched to, that bot's tag (neither is stored: they're added as the journal is built). `Filters.tags` holds tag ids, or `UNCATEGORIZED` (`none`, "No tags"); a trade passes if it has any of them. Search covers the tag names. `save_trade` replaces a trade's tags; `edit_many` adds (`add_tags` by name, `add_tag_ids` by id) and removes (`remove_tags`) tags on many trades. The Tags page (`/journal/tags`, `tags.html`) uses `add_tag`, `rename_tag`, `delete_tag` (refused, with the bot's name, for a tag a bot files under), `tag_counts` and `bot_tags`; `tag_for` finds a tag by name, any case, or makes it (a bot's **+ New tag**, the setup wizard, manual bots' "Manual" tag). Page totals (`summarize`, `journal_summary`) are over the shown entries, each once, however many tags it has.

**Migration 0054 (#286, decision [0113](../decisions/0113-stocks-value-from-holdings-and-fills.md)):** drops `stock_snapshots` (added by 0050 for the daily stock save of decision 0092). The Stocks tab's value chart is worked out from the brokers' holdings and the synced fills on each visit (`services/stock_value`, see [positions.md](positions.md#stocks-tab-236-286)), so nothing about stocks is stored. The downgrade recreates the empty table.

## Backups (`core/backups.py`, `services/backups.py`)
Backups use SQLite's online backup API (`sqlite3.Connection.backup`), so a copy is consistent even while the app writes. Each copy is gzip-compressed (#13; about 59 MB → 4 MB on real data), written as `….partial` and renamed when complete, and named `optionsjourney-YYYY-MM-DD-HHMM.db.gz` in New York time (older `.db` backups still count and restore). Only files with that name are ever deleted, beyond the `backup_keep` setting (default 30). **Browse…** runs the computer's own folder picker (`osascript choose folder` / a PowerShell FolderBrowserDialog) on the server, which is the user's computer. `start_scheduler` runs from `__main__` (never in tests): it checks at start and then hourly. Restoring backs up first, copies the backup over the live database with the same API, then runs migrations and clears the positions cache.

## Times are always UTC
- **The column type:** use `UTCDateTime` for every datetime column. It stores UTC and always hands back timezone-aware UTC datetimes.
- **Naive datetimes are refused:** passing one raises an error rather than guessing a time zone.
- **Showing times:** convert to Eastern time only for display, with the `market_time` template filter.

## Migrations
The app upgrades the database itself at startup (`db.migrate`) ([0015](../decisions/0015-database-migrations-from-day-one.md)). After changing `db/models.py`, write a migration:

```bash
uv run alembic -c src/optionsjourney/db/alembic.ini revision --autogenerate -m "what changed"
```

Then:
1. **Check the generated file** in `db/migrations/versions/`. Autogenerate is a starting point, not gospel. Number new migrations in sequence (`0002_…`).
2. **Run `uv run pytest`.** `tests/db/test_migrations.py` fails if the migrations and the models disagree.
3. **Keep data safe:** a migration must never drop trade data. If a column changes shape, copy the data across in the migration.
4. **Change tables in place** where foreign keys point at them (`batch_alter_table(..., recreate="never")`): rebuilding a table would, with foreign keys on, null out references to it. SQLite can't change a column's nullability in place, so such a column stays as it is. Example: migration 0027 (bots slice 13) adds `bot_exits.legs`, `anchor_symbol` and `watched` and fills them from the old `short_symbol`/`long_symbol`, which stay NOT NULL (new rows write `""` where a group has no such leg) until a later release drops them.
