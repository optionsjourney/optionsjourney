# Documentation

Documentation is a first-class part of this project. A feature isn't done until it's documented.

## Sections

- **[guide/](guide/)**: how to install and use the app. Written for people who aren't programmers.
- **[decisions/](decisions/)**: one short file per important decision: what we chose, why, and what it costs us. When a decision changes, we add a new file that replaces the old one rather than rewriting history.
- **[developer/](developer/)**: how the code works, for anyone who wants to change it:
  - [getting-started.md](developer/getting-started.md): run, test and configure the app
  - [project-structure.md](developer/project-structure.md): where everything goes, and the rules
  - [broker-credentials.md](developer/broker-credentials.md): credential providers, gateway mode and the trading guard
  - [transactions-and-sync.md](developer/transactions-and-sync.md): how trades get from the brokers into the app
  - [database.md](developer/database.md): tables, UTC times and migrations
  - [positions.md](developer/positions.md): how orders become positions with P/L
  - [charts.md](developer/charts.md): Charts, the risk graph and the Trade page's chain, from Tracker2, and the server side they expect
  - [bots.md](developer/bots.md): bots, their tables, entries and exits
  - [orders.md](developer/orders.md): the one path every order takes to a broker, the check, and unclear outcomes
  - [help-and-tooltips.md](developer/help-and-tooltips.md): the in-app Help pages, the ⓘ tips and the link tests
  - [releasing.md](developer/releasing.md): making a release and publishing it to the public repo

## Decisions so far

| # | Decision | Status |
| --- | --- | --- |
| [0001](decisions/0001-fork-tracker2-into-a-simpler-app.md) | Build a simpler app based on Tracker2 | Accepted, partly replaced by 0021 |
| [0002](decisions/0002-everyone-runs-their-own-copy.md) | Everyone runs their own copy, on SQLite | Accepted |
| [0003](decisions/0003-brokers-and-trade-input.md) | TastyTrade, Schwab and manual entry | Accepted |
| [0004](decisions/0004-automation-first-strategy-naming.md) | Automation first; simple strategy naming | Accepted, amended by 0021 |
| [0005](decisions/0005-shareable-bots-as-json.md) | Bots are shared as JSON files | Accepted |
| [0006](decisions/0006-dev-gateway-credentials.md) | Development borrows tokens from the author's server | Accepted |
| [0007](decisions/0007-guided-setup-wizard.md) | Guided setup wizard | Accepted |
| [0008](decisions/0008-menu-bar-launcher.md) | Runs as a menu bar / tray app | Proposed |
| [0009](decisions/0009-bot-safety-defaults.md) | Bots start in preview mode | Proposed (switch amended by 0028; who sets the size limits amended by 0058) |
| [0010](decisions/0010-documentation-in-the-repo.md) | Notes, decisions and docs live in the repo | Accepted |
| [0011](decisions/0011-project-structure.md) | A clean, conventional project structure | Accepted |
| [0012](decisions/0012-broker-apis-over-plain-http.md) | Call broker APIs over plain HTTP, and use plain SQLAlchemy | Accepted |
| [0013](decisions/0013-local-only-web-server.md) | The app only listens on this computer | Replaced by 0063 |
| [0014](decisions/0014-store-broker-transactions-raw.md) | Store broker transactions exactly as received | Accepted |
| [0015](decisions/0015-database-migrations-from-day-one.md) | Database migrations from the first table | Accepted |
| [0016](decisions/0016-group-fills-by-order.md) | Group fills by order, with opens and closes apart | Accepted |
| [0017](decisions/0017-positions-follow-tracker2.md) | Positions follow Tracker2's rules | Accepted, amended by 0020 |
| [0018](decisions/0018-iron-condor-split-is-a-choice.md) | Splitting an iron condor is the user's choice | Accepted |
| [0019](decisions/0019-journal-strategies-tags-dashboards.md) | The journal: one strategy, free tags, and dashboards | Replaced by 0072 (in part) |
| [0020](decisions/0020-matching-lessons-from-real-history.md) | Position matching: lessons from a year of real history | Accepted |
| [0021](decisions/0021-product-scope.md) | What the app includes, and what it leaves out | Accepted (the quote rule for both brokers refined by 0080) |
| [0022](decisions/0022-build-order-and-github-project.md) | Build order, tracked in a GitHub project | Accepted |
| [0023](decisions/0023-manual-entry-backups-account-values.md) | Manual entry, backups and account value history | Accepted |
| [0024](decisions/0024-charts-and-analyze-are-tracker2s-pages.md) | Charts and Analyze are Tracker2's own pages | Accepted (Analyze part replaced by 0031) |
| [0025](decisions/0025-strategy-tags-and-stocks.md) | Strategies also tag their trades; stocks get a tag and a tab | Accepted (tab replaced by 0026; strategy tagging replaced by 0072) |
| [0026](decisions/0026-stocks-tab-shows-broker-holdings.md) | The Stocks tab shows the brokers' holdings | Accepted |
| [0027](decisions/0027-bot-engine-and-managed-trades.md) | The whole bot engine comes over; stops and exits work on any trade | Accepted |
| [0028](decisions/0028-one-order-path-and-two-pause-switches.md) | One order path; "No new trades" and "Freeze all orders" replace "Stop all bots" | Accepted |
| [0029](decisions/0029-accounts-page-shows-stored-balances-first.md) | The Accounts page shows stored balances first, then fresh ones | Accepted |
| [0030](decisions/0030-bots-built-as-thin-slices-in-tracker2s-tables.md) | Bots are built as thin, working slices, stored in Tracker2's tables | Accepted |
| [0031](decisions/0031-trade-page-replaces-analyze.md) | The Trade page replaces Analyze; you can open trades yourself | Accepted |
| [0032](decisions/0032-economic-events-and-the-market-calendar-from-public-calendars.md) | Economic events and the market calendar come from public calendars | Proposed |
| [0033](decisions/0033-bot-file-format-version-1-shared-as-a-file.md) | The bot file format, version 1, shared as a file | Accepted (the file-only part replaced by 0056) |
| [0034](decisions/0034-watch-rules-read-delta-from-the-quote-stream.md) | Watch rules read delta from the quote stream | Proposed (the 30 s price limit refined by 0081) |
| [0035](decisions/0035-safety-alerts-always-reach-every-channel.md) | Safety alerts always reach every channel; bot alerts are sent off the bots' threads | Proposed |
| [0036](decisions/0036-new-entry-styles-only-where-the-exits-can-protect-them.md) | A new entry style ships only where the exits already protect it | Proposed |
| [0037](decisions/0037-schwab-bots-stops-are-watched-by-the-app.md) | A Schwab bot's stops are watched by the app, behind a switch | Proposed (switch replaced by 0059; watched stops by 0060) |
| [0038](decisions/0038-a-trade-you-opened-can-be-handed-to-a-bot.md) | A trade you opened can be handed to a bot | Proposed |
| [0039](decisions/0039-any-same-day-structure-with-a-stop-that-always-works.md) | A bot can open any same-day structure, and every stop it offers really works | Proposed |
| [0040](decisions/0040-legs-in-other-expirations-and-trades-held-overnight.md) | Bots can trade legs in other expirations and hold trades overnight | Accepted (replaces part of 0036; 60-day cap replaced by 0067) |
| [0041](decisions/0041-the-trade-page-can-send-a-bots-entry.md) | The Trade page can send a ticket as a bot's entry, and any shape can be handed to a bot | Accepted (amends 0031 and 0038) |
| [0042](decisions/0042-bots-trade-xsp-and-the-big-index-etfs.md) | Bots trade XSP, ETFs and stocks, and never hold an American-style option into expiry unannounced | Accepted (widens 0030) |
| [0043](decisions/0043-futures-bots-set-a-profit-target-only.md) | Futures-options bots set a profit target only, and enter only from the Trade page | Accepted (replaces 0036's stop rule for futures only) |
| [0044](decisions/0044-stop-and-target-switch-off-separately-and-missing-exits-are-restored-on-demand.md) | The stop and the target switch off separately, and missing exits are restored on demand | Proposed (replaces slice 6's single exits-off switch) |
| [0045](decisions/0045-fixed-dollar-sizing-and-trade-now.md) | A debit bot can size its entries by dollars, and a live bot can be traded now | Proposed |
| [0046](decisions/0046-pages-show-what-bots-do-and-only-read.md) | The pages that show what bots do only read; the account stream waits | Proposed |
| [0047](decisions/0047-per-trade-settings-and-closing-another-group-after-a-stop.md) | A trade can carry its own settings, a stop can close another group, and a futures trade's target and entry are changed in place | Proposed |
| [0048](decisions/0048-trades-take-either-side-and-each-group-chooses-what-rests.md) | A trade takes the side it prices on, and each group chooses whether its stop or its target rests at the broker | Accepted |
| [0049](decisions/0049-the-bot-tag-picker-offers-the-journals-tags.md) | The bot tag picker offers the journal's tags too | Accepted |
| [0050](decisions/0050-size-by-buying-power-asked-of-the-accounts-broker.md) | A bot sizes by contracts or by buying power, and the account's broker says what one contract needs | Accepted |
| [0051](decisions/0051-entry-times-may-name-a-second.md) | Entry times may name a second, and the Schedule shows only what's used | Accepted |
| [0052](decisions/0052-no-schwab-stream-in-gateway-mode.md) | No Schwab stream in gateway mode | Accepted |
| [0053](decisions/0053-leg-groups-like-optionomega.md) | Leg groups like OptionOmega | Accepted |
| [0054](decisions/0054-only-a-one-leg-group-rests-its-stop.md) | Only a one-leg group rests its stop | Accepted |
| [0055](decisions/0055-actions-can-fire-on-a-loss.md) | Actions can fire on a loss | Accepted |
| [0056](decisions/0056-bots-can-be-imported-from-a-link.md) | Bots can be imported from a link | Accepted |
| [0057](decisions/0057-tracker2-bot-files-import.md) | Tracker2 bot files import | Accepted |
| [0058](decisions/0058-contract-limits-can-be-worked-out.md) | Contract limits can be left blank and worked out | Accepted |
| [0059](decisions/0059-schwab-bots-need-no-switch.md) | Schwab bots need no switch | Accepted (same-day only lifted by 0060; preview default changed by 0061) |
| [0060](decisions/0060-schwab-bots-rest-their-exits-at-schwab.md) | Schwab bots rest their exits at Schwab, as TastyTrade bots do | Accepted |
| [0061](decisions/0061-bots-are-live-or-paper.md) | Bots are Live or Paper; Preview becomes a button | Accepted |
| [0062](decisions/0062-settle-expirations-at-the-close.md) | Settle expiring trades at the close, without waiting for the broker | Accepted (the "settle once at the daily closes" parts replaced by 0078) |
| [0063](decisions/0063-optional-local-network-access.md) | The app can optionally answer on the local network | Accepted |
| [0064](decisions/0064-history-starts-on-day-one.md) | Trade history starts the day the app is first used | Accepted |
| [0065](decisions/0065-keep-conceding-and-bidless-longs.md) | Keep conceding and Ignore bid-less longs, as Tracker2 | Accepted |
| [0066](decisions/0066-manual-bots.md) | Manual bots: reusable exit rules for a trade you opened | Accepted |
| [0067](decisions/0067-longer-legs-and-chases.md) | Legs up to 1,100 days out, chases up to 60 tries | Accepted |
| [0068](decisions/0068-match-trades-to-bots.md) | Match past trades to bots by filing them under the bot's strategy | Replaced by 0071 (in part) |
| [0069](decisions/0069-split-condors-any-grouping.md) | Split iron condors whatever their leg grouping | Accepted |
| [0070](decisions/0070-split-uneven-condors.md) | Split condors with uneven sides, and on Match trades | Accepted |
| [0071](decisions/0071-matched-trades-link-to-their-bot.md) | Matched trades link to their bot, and a bot's record follows the links | Accepted |
| [0072](decisions/0072-tags-only.md) | Tags only: strategies are retired, and a bot files its trades under a tag | Accepted |
| [0073](decisions/0073-chain-follows-the-tickets-broker.md) | The Trade page's chain follows the ticket's broker and says where it came from | Accepted |
| [0074](decisions/0074-deleted-bots-tag-goes-onto-its-trades.md) | A deleted bot's tag goes onto its trades as their own | Accepted |
| [0075](decisions/0075-arm-and-switch-many-bots.md) | Arm, turn off and switch Live or Paper for many bots at once | Accepted |
| [0076](decisions/0076-manage-panel-on-the-journal.md) | A Manage panel on every journal row, built from the full pages' own sections | Accepted |
| [0077](decisions/0077-trade-popups.md) | Trade popups, recorded from the journal after each sync | Accepted |
| [0078](decisions/0078-pl-at-the-close-from-either-broker.md) | P/L at the close from either broker, corrected to the official close (replaces part of 0062) | Accepted |
| [0079](decisions/0079-schedule-drawer.md) | Today's schedule as Tracker2's drawer, skipping any day shown (replaces part of the slice 20 spec) | Accepted |
| [0080](decisions/0080-schwab-answers-when-tastytrade-is-down-or-stale.md) | Schwab answers when TastyTrade is down or stale (refines 0021) | Accepted |
| [0081](decisions/0081-watch-rules-hold-30-seconds-and-quiet-quotes-stay-fresh.md) | Delta and ratio watch rules hold 30 seconds; a quiet quote on a live stream stays fresh (builds on 0034) | Accepted |
| [0082](decisions/0082-offset-placed-shorts-may-be-in-the-money.md) | A short placed by an offset may be in the money; its 0.90 delta check stays | Accepted |
| [0083](decisions/0083-blackout-lists-before-expiration-and-market-holidays.md) | Blackout lists checked on the entry day or before expiration, and a Market holidays list | Accepted |
| [0084](decisions/0084-close-group-counts-single-contracts-of-a-lot.md) | Close group counts single contracts of a ×N group, with no split close | Accepted |
| [0085](decisions/0085-exit-chase-retries-a-close-the-broker-cancels-at-once.md) | The exit chase sends a close again once when the broker cancels it at once, and takes the old order's fill when a replacement dies | Accepted |
| [0086](decisions/0086-tracker2-dollar-amounts-divided-by-the-groups-gcd.md) | A Tracker2 bot's $ amounts are divided by each group's gcd on import, so they keep their size (refines 0057) | Accepted |
| [0087](decisions/0087-schedule-drawer-shows-the-days-events-and-holidays.md) | The schedule drawer shows the day's economic events and names the holiday (builds on 0079) | Accepted |
| [0088](decisions/0088-help-inside-the-app-renders-the-guide.md) | `/help` renders the guide inside the app, with a Help link in the nav | Accepted |
| [0089](decisions/0089-chart-news-from-finnhub-and-plex-fonts-in-the-app.md) | Charts' News panel from Finnhub with an optional key; IBM Plex fonts stored in the app | Replaced by 0090 in part |
| [0090](decisions/0090-chart-news-works-without-a-key.md) | Chart news works without a key (Yahoo Finance), Finnhub when a key is set | Accepted |
| [0091](decisions/0091-after-hours-marks-from-the-last-real-quote.md) | After hours, an option with no market is priced at its last real mark | Accepted |
| [0092](decisions/0092-stocks-performance-from-daily-saves.md) | Stocks performance comes from daily saves of holdings and closes, after each close | Accepted |
| [0093](decisions/0093-public-release-license-and-disclaimer.md) | Public releases: PolyForm Noncommercial, a trading disclaimer, and a separate public repo | Accepted |

## Writing a new decision

Copy this into `decisions/NNNN-short-title.md`:

```markdown
# NNNN. Title

- **Status:** Proposed | Accepted | Replaced by NNNN
- **Date:** YYYY-MM-DD

## Context
What problem or question led to this.

## Decision
What we chose.

## Why
The reasons, and the options we turned down.

## Consequences
What this makes easier, what it makes harder, and what we have to do because of it.
```
