# Changelog

Notable changes to OptionsJourney. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-21

The first public release.

### Added
- Paper trading: a bot is Live or Paper. A Paper bot follows all its rules but sends nothing: the broker checks each order, the app records a pretend fill at the mid price, then watches every stop, target, timed close and expiry itself. Paper trades have their own view in the Journal (Paper trades) and never touch the Journal's numbers; the Automation list shows a Paper bot's paper P/L and win rate. Switching a bot to Live closes its open paper trades first.
- Preview order: a button on a bot's page that checks the order the bot would send now with your broker and shows a risk graph. It sends nothing and saves nothing.
- Alerts can also go to Discord (a webhook) and/or Telegram (a bot), set in Settings → Notifications, with Send a test.
- Your own alerts: a price going above or below a level, or a trade's open P/L, checked every 15 seconds from live prices; shown on the Alerts page and in the top bar until dismissed, and can be set again.
- Analyze: model an options position from the live chain (click bid to sell, ask to buy) or open a trade from the journal: P/L at expiration and today, days-ahead and IV sliders, ±1 SD band, max profit/loss, breakevens, chance of profit and greeks.
- A Charts page: a grid of 1, 2, 4 or 6 candlestick charts (1m to 1d), each with its own symbol, EMA 20/50, VWAP and volume, a crosshair, zoom and pan, and a pop-out window. Prices come from TastyTrade first, Schwab as backup; the layout is remembered.
- Futures options (/ES, /GC, …) get live open P/L too, priced from TastyTrade.
- Live Open P/L on the journal: a box in the summary and each open trade's figure in the list, from the brokers' live prices, updated every few seconds.
- Live prices from the brokers' streams (TastyTrade's DXLink first, Schwab's streamer as backup), for the pages that need them (`/api/quotes`); stream status in Settings → Brokers.
- Install scripts: double-click `scripts/Install OptionsJourney.command` (Mac) or `.bat` (Windows). Installs uv, Python and the app into the user's folder only, makes a double-click launcher, and starts the menu bar app.
- A menu bar app (`optionsjourney-menubar`): runs OptionsJourney in the background with a status icon showing alerts, Open OptionsJourney, Start at login, Keep awake during market hours, and Quit.
- A setup guide on first start (and from Settings → Run setup again): brokers, connecting TastyTrade and Schwab (or finish later), trading style → starting strategies, backups, first sync.
- Connect Schwab yourself (Settings → Brokers): app details, log in at Schwab, paste the address you land on. Reconnect Schwab for the weekly login, with an alert a day before it runs out.
- Connect TastyTrade yourself (Settings → Brokers, `local` mode): paste the client secret and refresh token, checked with TastyTrade before they're kept in the keychain.
- Broker logins for `local` mode are kept in the system keychain (macOS Keychain or Windows Credential Manager), never in the database or backups.
- Expired trades waiting on the broker show an estimated P/L from the broker's closing price (TastyTrade first, Schwab's history as backup) until the broker's record arrives.
- Alerts (top bar): a count of things that need attention, such as a failed sync, a sync more than 3 days old, or a failed backup, each linking to where to fix it.
- Backups (Settings → Backups): daily or weekly while the app runs, to a folder you choose, keeping the last 30; Back up now; Restore, which backs up first so it can be undone.
- A Settings page (top bar → Settings): every setting explained, checked before saving. First section: Backups.
- Adding trades by hand (Journal → ＋ Add a trade) for brokers the app doesn't sync: manual accounts, up to 4 legs, closing, marking expired and deleting entries.
- Account values over time (Accounts → Account values over time): every account's daily value, with deposits and withdrawals marked and a gain that leaves them out. TastyTrade history is filled in a year back; Schwab is recorded each day the app reads it.
- The journal's summary now resembles Tracker2's: Closed P/L, Open Premium, Premium, per-lot averages and extremes, Trades and Winners, and Win Rate and Capture Rate dials.
- Trading metrics page (Journal → Trading metrics): win rate, expectancy, profit factor, max drawdown, trade averages, P/L by day/week/month with bars, and day-of-week stats; follows the filters and dashboards.
- A **P/L** view next to the trade list, with the P/L calendar and "P/L over time" charts: cumulative realized P/L and drawdown, with a crosshair, keyboard support and a monthly table.
- P/L calendar (in the P/L view, for every dashboard): P/L per day and week, trades closed, winning days, best and worst day; follows the filters.
- Project documentation: roadmap, decision records, journal, and project structure.
- App skeleton: Flask app, settings, local SQLite database, `/health`.
- Broker credentials layer with `local` and development-only `gateway` modes.
- Trading guard: blocks orders and token refresh in gateway mode, allows previews.
- TastyTrade and Schwab accounts and balances, shown on the Accounts page.
- Transaction sync from TastyTrade and Schwab (1 year back, then incremental), stored exactly as received.
- Transactions page: every fill, filterable by account and period, with a Sync now button.
- Database migrations, applied automatically at startup.
- Transactions grouped into orders (opens and closes apart), each named by its structure: "SPX Iron Condor", "SPX Put Credit Spread", ….
- Positions: every trade from open to close (partial closes, rolls, expirations, assignments), with entry/exit per spread, realized P/L and fees, following Tracker2's rules.
- Journal (the new home page): every trade with filters by strategy, tag, account, status and period; dashboards as saved views with summary stats; per-trade name, strategy, tags, notes and close reason; Split / Combine for iron condors; managing strategies and tags.
- User guide: "Your trade journal".

### Changed
- Analyze is gone; the Trade page replaces it. Charts' ↗ Analyzer button is now ↗ Trade and opens the Trade page on the linked symbol. Old Analyze links open the Trade page, or a trade's risk graph.
- New bots, imported bots and clones of Live bots are Live and switched off; arming a Live bot always shows a confirmation page first. What used to be "preview mode" is now Paper. The reserved tag and filter "Preview" is now "Paper" (a tag of your own named "Paper" is dropped when the bot is saved). The Preview now button is Preview order, and the Automation filter "Preview" is "Paper".
- Shares are one running position per stock per account: buys and dividend reinvestments add to it, and sales are priced at the average cost (on the real history, open trades went from 133 to 66).
- Backups are compressed (about a tenth of the size), how many to keep is a setting, and the folder can be picked with Browse….
- Much faster journal: building positions went from about 17 s to under 1 s on a year of history; built positions are kept in memory between page loads (about 0.1 s); long lists show 100 trades or 200 rows per page.

### Fixed
- On phones, the top navigation wraps and wide tables scroll inside their card, so pages no longer scroll sideways.
- Treasury bills close when they mature, instead of staying open with the maturity listed as unmatched.
- Schwab option legs that arrive without a symbol get it rebuilt from their details, so they can't be matched to the wrong contract.
- Trades whose options have expired show as "Expired, waiting on broker" until the broker posts the expiration record, instead of staying Open.
- The journal crashed on a corporate action on shares; such broker events are now listed, not applied.
- Expiration records covering several positions, cash-settled assignments and exercises, closes spanning positions, and same-second broker corrections now close the right trades.
