# Changelog

Notable changes to OptionsJourney. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-10-24

Save trades as ideas and see how they would have done, every order waiting at your brokers in one place, a Stocks page, and a better bell.

### Added
- **Ideas:** press **Idea ☆** on the Trade page to save the trade you built. The Journal's **💡 Ideas** drawer follows how it would have done, and **Trade it** brings it back to the ticket. See the guide's Ideas page.
- **Pending orders:** the Journal's **⏳ Pending** drawer lists every order still waiting at your brokers, in every account, with live prices. You can change the price of, or cancel, the orders you sent from the app; the others are shown and changed where they came from.
- **Stocks:** the Journal's **Stocks** tab shows every share position in your connected accounts, with live prices. Three cards at the top add them up: **Total P&L** (against what you paid), **Total market value** and **Day P&L**. Under them, a **Portfolio value** chart shows what your shares were worth at each day's close since the app first synced, and two tabs list your **Holdings** and your **Transaction History** (every stock buy, sell, assignment and exercise the app has synced). See the guide's Journal page.
- Clicking the bell opens a list of **recent alerts** under it: problems and notes first, then the newest alerts with the new ones highlighted, and a link to the whole Alerts page.
- An alert that goes off now pops up on whatever page you're on, with a chime, like a trade popup. That includes your price and trade alerts, your bots' alerts, and P/L corrections.
- The Alerts page has two tabs. **Price alerts** lists every alert, yours and your bots', with its condition, the live value, whether it's armed, went off or is off, and when it went off, with filters and Turn off / Set again / Delete on each row. **Trades** lists the trades of the last day, as before.

### Fixed
- The red number on the bell goes down once you've seen what's new. Notes like "Schwab last synced 3 days ago" no longer keep it up; real problems still do until they're fixed.
- The Alerts page's history is headed **Recent**, not "Unread".
- No more alert every evening saying today's expirations settled. You only hear about it when something differs: the official close corrected a trade's P/L, or your broker's record doesn't match.
- "Last synced N days ago" and "didn't sync" only show for brokers you've connected, and not for an account you've closed.

## [0.4.0] - 2026-10-22

Open the app from your other computers and your phone, and Help that searches as you type.

### Added
- **Settings → This computer → Open from other devices.** Turn it on, restart the app, and your other computers can open it at this computer's address, and your phone can too over Tailscale. It's off unless you turn it on, and it stays on through updates. There's no login, so only use it on networks you trust: see the FAQ.
- Help's search box shows results as you type, without pressing Enter. Clearing it takes you back to the page you were reading.

### Fixed
- In the demo, the tour no longer skips its profit-target step when the Weekly calendar's target filled before you got there.

### Changed
- The Troubleshooting page is now the **FAQ**, with new questions about keeping the computer on, where your data is kept, using the app from other devices and putting it on the internet.

## [0.3.0] - 2026-10-22

A demo mode you can try at demo.optionsjourney.com, and small fixes.

### Added
- Demo mode: the real app on a pretend broker and a made-up market, with a guided tour. Nothing in it is real money, and it never connects to a broker. It's what runs at demo.optionsjourney.com; the app you install works as before.
- The guide has a page on getting your TastyTrade and Schwab API keys, step by step.
- The install guide shows the menu bar and tray icon.

### Fixed
- An overnight check no longer says a closed calendar is still held when its near legs have expired.
- On the Trade page, the risk graph's Close button is no longer hidden behind the page's banners.
- Number columns' headings line up with their numbers.

## [0.2.2] - 2026-10-21

### Fixed
- After **Restore the backup from before the upgrade**, the app starts again by itself (it quit without restarting).

### Added
- The guide's Updating page has pictures of each step, and of the page you see when going back.

## [0.2.1] - 2026-10-21

The first update you can install from the OJ menu.

### Fixed
- On Windows, the installer window opened from the OJ menu stays open on any error, so you can read it.

### Changed
- A small change to how your data is stored, which changes nothing you'll see. Because of it, the app backs up your data before upgrading it, as it will for every update that changes the data.

## [0.2.0] - 2026-10-21

Updates from the OJ menu, and fixes from testing on fresh Macs and Windows PCs.

### Added
- The version you have shows in the OJ menu (● Running · v0.2.0) and at the bottom of every page.
- Checking for and installing updates from the OJ menu: once a day the app checks GitHub for a newer version, and the menu offers **Download** and then **Install**. Turn the check off in Settings → This computer.
- A backup before every data upgrade, kept in the backup folder (or in Documents/OptionsJourney Backups if that folder can't be reached); the newest 3 are kept.
- A page that says what to do when the app can't start, including a button to restore the backup from before an upgrade.

### Changed
- A new logo, and a menu bar / tray icon you can read.
- The installer puts the old version back if an update fails.
- The installers are named for the computer they're for: **Install OptionsJourney for Mac.command** and **Install OptionsJourney for Windows.bat**.
- Connecting a broker for the first time asks you to tick that you're responsible for every order placed in your accounts.

### Fixed
- Clicking the text of a tip next to a tick box no longer ticks the box.
- The app reported its version as 0.1.0 whatever version was installed.
- On a fresh Mac, the installer no longer pops up "install the command line developer tools".

## [0.1.1] - 2026-10-21

Fixes from testing on a fresh Windows PC.

### Changed
- The installer puts the app in a fixed folder (`AppData\Local\Programs\OptionsJourney` on Windows, with a Start menu entry; `~/Applications/OptionsJourney` on a Mac, with an OptionsJourney app for Spotlight and Launchpad). Running a newer installer updates it; your data is kept.
- On Windows the app runs without a console window.

### Added
- A log file (`logs/optionsjourney.log` in the data folder), and **Show app in Finder/Explorer**, **Open logs** and **Restart OptionsJourney** in the OJ menu.
- A "Something went wrong" page that says what to do, instead of a bare server error.

### Fixed
- On Windows, the Journal failed with a server error once a trade had closed.
- The menu bar app now syncs on its own, like `uv run optionsjourney` did.

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
