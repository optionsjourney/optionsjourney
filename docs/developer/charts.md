# Charts and the Trade page: Tracker2's pages

[Decision 0024](../decisions/0024-charts-and-analyze-are-tracker2s-pages.md): the chart grid is Tracker2's own JavaScript, copied as it is; the Trade page's chain and the risk graph are too (decision 0031). Analyze, Tracker2's analyzer, was removed (#223). This app provides the server side they expect. This page is that contract, and the list of every change made to the copies.

## Files
- `web/static/js/chart_grid/`, `js/charts/` and `css/chart_grid.css`: copied from Tracker2's `src/static/`. They only import each other, by relative paths.
- `web/static/vendor/`: the libraries they need, at the versions Tracker2 loads from CDNs:
  - IBM Plex Sans Condensed and IBM Plex Mono, 400/500/600, latin (`vendor/fonts/`, SIL Open Font License, from `@fontsource` 5.1.0), which Tracker2 loads from Google Fonts (decision 0089).
  - Lightweight Charts 5.0.0 (TradingView, Apache 2.0; the TradingView logo it shows on each chart is its attribution).
  - The Socket.IO 4.7.4 client (MIT).
  - The Tailwind CSS 3.4.16 browser build (MIT).
- `web/static/js/alert_chime.js` and `popout.js` (the `_popoutUrl` function from Tracker2's `risk_graph.js`); `web/static/tos-favicon.ico`.
- Templates: `tracker_base.html` (Tailwind dark, with this app's top bar and footer: `_nav.html`, `_footer.html` and `css/nav.css`, shared with `base.html`), `chart_bare_base.html` (no top bar, for second-monitor windows), `chart_grid.html` and `chart_grid_popout.html`.
- `asset_url(filename)` is a template function for static files, as in Tracker2.

## Server side
| Piece | Where |
|---|---|
| Pages: `/charts` (also `/chart/grid`, `?bare=1`), `/chart/grid/popout` | `web/routes/chart_grid.py` |
| Layouts, tabs, drawings, watchlist groups (REST, Tracker2's answers and errors) | `services/chart_grid.py`, tables from migration 0006 |
| `/api/chart/pricehistory/<symbol>`, `/futures_contract`, `/prev_close` | `services/chart_market.py` |
| `/api/news/<symbol>` (the News panel): `{symbol, news: [{headline, source, url, summary, datetime}], note?}`, or `error` with 502 | `services/news.py` (Yahoo Finance's per-ticker RSS feed, or Finnhub when the `finnhub_api_key` setting is set; 5-minute cache per source and ticker; decision 0090), `core/news.py` (parsing; only http/https links kept) |
| Live candles over Socket.IO | `services/chart_feed.py`, bars in `core/chart_bars.py` |
| Watchlist quotes over Socket.IO | `services/watchlist_quotes.py` |
| The option chain: `/api/trade/expirations`, `earnings`, `held-legs`, `chain-table`; old Analyze addresses (`/analyze`, `/tools/analyzer/`) redirect | `web/routes/chain.py` |
| The chain's data (`ChainData`: expirations, chain table, futures series, the underlying's price, earnings), also read by the bots | `services/chain.py` |
| Ticker check, expiration filter, futures series and symbols, held legs | `core/chain.py` |
| Trading sessions (regular, Curb, overnight, futures) | `core/sessions.py` |
| The risk graph's live leg quotes over Socket.IO | `services/risk_quotes.py` |
| Socket.IO events | `web/sockets.py` |

Errors are `{"error": true, "error_message": "..."}` with Tracker2's status codes.

### Bars
- Bar times are seconds. Intraday bars are **New York wall clock written as a UTC epoch** (a 10:30 New York bar is stamped 10:30 UTC), so the charts show New York time wherever the user is.
- Daily, weekly and monthly bars are 00:00 UTC of their first session date.
- Stocks and indexes keep 09:30 to before 16:00. Futures keep every hour they trade.
- Bars with no real open are dropped.

### Socket.IO events
| From the page | Answer |
|---|---|
| `subscribe_candles {symbol, interval}` | `chart_candles_snapshot {symbol, interval, bars}` once, then `chart_candle_update {symbol, interval, bar}` per change. `chart_error` for a symbol or interval it can't chart. |
| `unsubscribe_candles` | |
| `watch_drawings` / `unwatch_drawings {symbol, interval}` | `chart_drawings_changed {symbol, interval}` when anyone saves them |
| `subscribe_risk_quotes {symbols}` (DXLink symbols: `SPX`, `.SPXW261017C5800`) | `risk_quotes {quotes: {SYMBOL: {bid, ask, last}}, ts}` with changed quotes only, about once a second |
| `subscribe_watchlist_quotes {symbols}` | `watchlist_quotes {quotes: {SYMBOL: {last, net_chg, net_pct, mark, mark_pct}}, source, ts}` with changed rows only, about once a second |

- **Candle symbols:**
  - Stocks and indexes by ticker.
  - Futures by DXLink contract (`/ESZ26:XCME`). Pages look one up from a root with `/api/chart/futures_contract`.
- **The candle feed** has its own DXLink connection. It connects for the first viewer, subscribes what viewers want each second, and disconnects a minute after the last one leaves.
- **DXLink periods:** hour bars are asked for as `{=h,a=s}`; `{=1h,a=s}` returns nothing.
- **Without TastyTrade** nothing streams. After ten seconds the pages fall back to `/api/chart/pricehistory`, which uses Schwab.
- **Watchlist rows:**
  - Prices come from the quote hub. The previous close comes from Schwab's quotes (or TastyTrade's), refreshed every 20 seconds.
  - For stocks it's the regular session's last price minus its net change, because at the weekend Schwab's `closePrice` is already Friday's.
  - Futures roots (`/ES`) come from the quote snapshots only.

### The option chain
- Answers are `{"status": "success", ...}`, and errors `{"status": "error", "message"}`, with 400 for bad input and 503 when there's no chain.
- **Chains:**
  - Schwab's chain snapshot first: one call, every strike, with greeks. Only contracts on the root Tracker2 prices are used (SPXW for SPX, RUTW for RUT).
  - Without Schwab, TastyTrade's quotes through the quote hub, for the 120 strikes nearest the price. It waits up to 4 seconds for them to fill in.
  - `?broker=` on `chain-table` asks for one broker first (the ticket's account), falling back to the other.
- **Futures** (`/ES`, `/GC` …, #53), as Tracker2's `analyzer_futures.py`:
  - **Series:** each date's series comes from TastyTrade's nested futures chain (`/futures-option-chains/{root}/nested`). AM-settled series are left out. Where a date has several series, the one with the most strikes wins.
  - **Multiplier:** `notional-value` × 100 (/ES 50, /GC 100).
  - **Quotes:** the 120 strikes nearest the contract's price stream from DXLink through the quote hub. TastyTrade's REST quotes fill what hasn't priced yet, in batches of 50.
  - **No Schwab:** Schwab has no futures options, so futures need TastyTrade.
- **Spread alerts** (#54): the Trade page's Alert button posts to `/api/alerts`, answered from the app's own alerts (`services/user_alerts.py`). `kind: spread` is the size of the legs' net mid, `abs(sum(qty × mark))`, as Tracker2 measures it. A fired price or spread alert links to `/trade?ticker=`.
- **Removed with Analyze (#223):** the modeler, its ThinkorSwim-line hand-off and chain summary, the settlement and "spot at a time" bars, the chart pop-out and the `/api/analyzer/*` addresses. `/analyze?trade=` and `?position=` redirect to `/risk-graph?trade=`; any other `/analyze` or `/tools/analyzer/` address goes to `/trade` with its ticker.

## Risk graph
Tracker2's retired risk graph, which the journal opens for a trade (#91, spec 2026-10-12-trade-page-design).
- **Copied unchanged:** `static/js/modeler/pricing.js` (Black-Scholes, implied vol, the payoff curves and Greeks, pure) and `static/js/modeler/render.js` (draws the chart on a canvas).
- **Copied with cuts:** `static/js/risk_graph.js` is Tracker2's lines 1–658: the panel's state, sliders, slices, live quotes (`_riskLive`) and `_mountRiskGraphGroup`. Left out: Tracker2's "Open in Analyzer" link and pop-out helpers after it. `templates/_risk_graph_panel.html` is Tracker2's without that link.
  - The journal's drawer: `templates/_risk_drawer.html` and `static/js/risk_drawer.js` (Tracker2's analyzer drawer): the risk graph in an iframe (`/risk-graph?trade=…&embed=1`), opened by the trade rows' chart icon (`window.riskLinkClick`) and **Risk graph together** (`window.riskDrawer.open`); a modified click opens a new tab.
- **Ours:**
  - `core/risk_graph.py` turns a journal `Position` into Tracker2's chart-data legs (per-contract entry price, + received / - paid; the quantity still open; the multiplier; the DXLink stream symbol).
  - `services/risk_graph.py` adds live marks from the quote hub and the underlying's price.
  - `web/routes/risk_graph.py` serves `/risk-graph?trade=k1,k2` (`&embed=1` for the drawer and the grid) and `/api/risk-graph`.
  - `static/js/risk_graph_page.js` fetches the trades and hands them to `_mountRiskGraphGroup`.
  - The journal's grid view (`/journal/grid`, `templates/journal_grid.html`, `static/js/journal_grid.js`; #215, Tracker2's grid view a633e6f4 and 0e7b5be4): a table of the filters' open option trades (up to 200), a box per trade, all ticked to start up to `risk_graph.MAX_TRADES`, and one `/risk-graph?trade=…&embed=1` iframe of the ticked ones, reloaded 600 ms after the last click. The header box ticks all or none; each account's total ticks only that account. Totals (open P/L and its % of the premium, per account) cover the ticked rows, from `/api/open-pl` with the same filters every 5 s (`plText` from `open_pl.js`). The totals line's button folds the table away, remembered in `localStorage` `journal_grid_table_collapsed`. Tracker2 keeps its selection to one underlying because its Analyzer takes one; our risk graph has a tab per underlying, so any trades can be ticked together. The pure parts (`totals`, `allOrNone`, `onlyAccount`, `toggled`, `graphHref`) are tested in `tests/web/static/js/journal_grid.test.js`.
- **Live quotes** use the `subscribe_risk_quotes` socket. Futures options have no stream symbol yet, so their graphs stay at the opening snapshot.
- **Tests:** `tests/web/static/js/modeler/pricing.test.js` is Tracker2's, run on Node's built-in runner through `tests/web/static/js/vitest_shim.js`: `node --experimental-default-type=module --test tests/web/static/js/`. One test's fixed expiry date was moved 30 days out, because the original date has passed. `render.test.js` isn't copied: it needs jsdom (an npm package), so the renderer is checked in the browser.

## Trade page
The page that replaced Analyze ([decision 0031](../decisions/0031-trade-page-replaces-analyze.md), #92): a chain, an order ticket and slice 1's risk graph.
- **Files** (`static/js/trade/`):
  - Copied from Tracker2's `analyzer/`: `chain_tray.js`, `chain_stats.js`, `chain_earnings.js` and `trade_ticket.js` (the default limit, credit/debit and overnight rules, pure).
  - `chain_panel.js` is an inline copy of Tracker2's `analyzer/chain_panel.js` with its own changes (below).
  - Ours: `tos_parse.js` (#312, pure: Tracker2's `modeler_tos_parse.js` grammar, plus `firstTosOrder` and `tosTray`, which matches a parsed order's legs to chain rows; all legs or none, no futures or [AM] legs). `trade_page.js`'s **Paste ThinkorSwim order** box switches the ticker, fetches each expiration's rows through `chain_panel`'s `rowsFor` (the panel's broker), sets the tray, then the limit and credit/debit. It never reviews or sends.
  - Ours: `ticket_core.js` (pure: turns the legs into the review request, the risk graph's legs and the spread alert's contracts) and `trade_page.js` (wires the chain, ticket, graph and alert together).
- **Routes** (`web/routes/trade.py`): `/trade` (`?ticker=`), `/api/trade/review`, `/api/trade/confirm`, `/api/trade/working`, `/api/trade/cancel` and `/api/trade/replace`. The chain's own routes are in `web/routes/chain.py` (above).
- **Server:** `services/trade_ticket.py` builds the broker-neutral `Order`. `review` sends it through `services/orders.submit` with `origin="you"` and `preview_only=True`.
- **Review codes and Confirm** (slice 3, #94): when a preview passes, `review` keeps the checked `Order` in an in-memory store (`_PENDING`, behind a lock) under a random one-time code and returns the code with a plain-words summary (built from the order after price rounding, so it shows what is sent). `confirm(code, broker, account)` takes the code out of the store first (one use; unknown, used, expired after `REVIEW_TTL` = 5 minutes, or another account's all refuse with "Review again"), re-reads the positions, refuses if the effect changed (roll, or opening vs closing), then calls `orders.submit` with `preview_only=False`. **It is the only live send for origin "you".** The code lives in memory only, so a restart drops pending reviews. The page shows Send only for the last review's code and drops it on any ticket change or Clear.
- **Working orders** (`services/trade_orders.py`, #90, #292, #293): `working` lists the broker's working orders, each with its `owner` (`Owner`: `kind`, `name`, `link`), `can_change`, `can_cancel`, `reason`, `category` (`core.pending.category`, today in market time) and `price`, plus the older `yours` flag (`kind == "you"`). `owner(session_factory, broker, account, found)` decides, in order: (1) the newest `OrderLog` row for that broker order id with `ok` true: origin `you` with action `send` or `replace` is **you**, origin `bot` is **bot** (the bot's name, and `/automation/runs/<bot_run_id>` when the row has one), origin `managed` is **managed**; (2) on TastyTrade, the newest row whose `tag` is the order's external identifier, ok or not (an order the broker accepted without its id coming back); (3) a bot run that carries the order id (`bot_adopt.owner`) makes it the bot's; (4) an order carrying the app's own tag with nothing above (TastyTrade: an external identifier of exactly 20 lowercase hex characters, the form `services/orders` uses; Schwab: the account's learned app tag) is **unknown** (no actions); (5) anything else is **outside** (placed at the broker or in another app). Only **you** can be changed or cancelled (decision 0115); every other owner is listed read-only with its `refusal` as `reason`: outside is "Placed outside the app. Change or cancel it at your broker.", unknown says the app can't tell which part of it owns it, and a bot's or managed order is "Managed by {name}, use its controls." (no name: "a bot" or "this trade's rules"). An order the broker reports as already being cancelled or replaced (`BrokerOrder.changing`) has no actions either. Failed or refused attempts don't count, so a failed cancel leaves the order as it was, and a cancel that went through ends it. `cancel` and `replace` read the owner again at call time (reading the order from the broker first when no ok row names its owner) and refuse anything but yours; yours go through `orders.cancel` / `orders.replace` with origin `you`, so Freeze, the check, **Largest order you can place**, the preview, the account lock and the `OrderLog` all apply. `replace` keeps the legs and the credit/debit side, and only the price changes. It reads the order fresh (`get_order`) and refuses with no broker write if it can't be read, isn't working, or any leg's `remaining` differs from its `quantity`: the replace would go out at the full size, the same rule as `bot_chase`. Your order is rebuilt from its logged `Order`, never from the broker's copy. The working list shows a part-filled order of yours with `can_change` false and `PART_FILLED` as `reason`; the page (`workingRows`: `from`, `canChange`, `canCancel`, `reason`) shows the price box and Replace only when `canChange`, Cancel when `canCancel`, and otherwise the reason.
- **Safety rule:** nothing on this page reaches the broker except through `orders.submit`, `replace` and `cancel`, the central check. The trading guard (gateway mode only) stays on; nothing here goes around it. Broker and guard error text reaches the page as it came (masking account numbers in it is an open item). `trading/orders.order_effect` (pure) says whether the legs open, close or mix (a roll, refused).
- **The central check** (`trading/check.py`) now lets your own opening orders through, within the setting **Settings → Your orders → Largest order you can place** (`max_order_contracts`, default 10; unreadable means 0, which refuses).
- **Rendering:** the ticket re-renders only when its structure changes (legs, credit/debit, time in force). Quote pushes patch prices in place, so a box you're typing in keeps its focus. The graph panel is hidden by toggling the `hidden` class (not the `hidden` attribute), because Tailwind's `flex` on the same element overrides the attribute's `display: none`, while `.hidden` comes after `.flex` in Tailwind's output.
- **Futures:** the chain and graph work, but review refuses them for now (#93).
- **The ticket's strip (#211, Tracker2's ac941c41):** `trade_page.js`'s `stripHtml` leads with the net price per unit (`chain_tray.netPrices`: quantities divided by their gcd, `unit` shown as ×N; Nat = sells at the bid and buys at the ask, Opp the reverse; + credit), large, green for a credit and red for a debit, then `chain_stats.stripItems`. The strip is redrawn on every quote push (`refresh`), so the price is live; it holds no inputs. Tracker2 fills BP Eff., BP After and Fees from its debounced bp-preview; this page has no preview call, so they come from the last **Review order** of the same ticket (`reviewCosts`, kept with the ticket signature; `ticket_core.reviewBp` turns `costs` into the preview's shape, Fees = commission + fees) and are dashes otherwise. The old "Mid … for the whole ticket" line is gone.
- **Held positions (Pos column, #210, Tracker2's f41682c5):** `GET /api/trade/held-legs?ticker=` (Tracker2's `/api/analyzer/held-legs`, in `web/routes/chain.py`) returns `{status, ticker, legs: [{account_id, expiration, strike, type, qty}]}`. `services/journal.held_legs` takes the journal's open trades (through the positions cache; closed trades and trades past expiry waiting on the broker are left out), maps each trade's (broker, number) to its `accounts.id`, and `core/chain.held_legs` nets the live option legs (`remaining`, signed long + / short −) per account, expiration, strike and call/put, dropping zero nets. `core/chain.holds_ticker` matches a leg to the chain: a futures root (/GC) matches every contract month (/GCZ6), and a weekly root (SPXW) its index. On the page, `trade/chain_held.js` (pure) gives each account a fixed colour by id, picks the legs at a cell and the expirations with something held; `chain_panel.js` loads the list with the ticker and every 30 s, draws the Pos pills, the header's Positions menu (all accounts or one) and the dots on the expiration tabs. `trade_page.js` passes the page's accounts (`opts.accounts`) for the names.

## Changes to the copies
Keep this list complete, so the copies can be refreshed from Tracker2 later.
- `chart_grid/dashboard.js`: Tracker2's "↗ Analyzer" button is **↗ Trade** and pops out `/trade?ticker=` on the linked panes' ticker (Tracker2: `/tools/analyzer/`), #223. Otherwise it's Tracker2's c1105aed, News panel included (#238).
- `chart_grid/pane.js`, `chart_grid/indicators/session_profiles.js` and `charts/candle_router.js` import `mergeBar` and `futuresSessionKey` from `charts/bars.js`, the two parts of Tracker2's `analyzer/chart_lines.js` still used. `pane.js` keeps the options for a pane hosted outside the grid (Tracker2's analyzer charts), unused here.
- `css/chart_grid.css` (Tracker2's c1105aed, #238): `@font-face` rules for IBM Plex in `vendor/fonts/` replace the Google Fonts `@import`, because the app works offline; `--cg-nav-h` is this app's top bar (61px); `.ind-has-tip` rows for `chart_tips.js`; `.grid-trade-btn` for `.grid-analyzer-btn`.
- `chart_grid/news.js` (the News panel and the watchlist/news column) is Tracker2's, unchanged. Without a Finnhub key the server reads Yahoo Finance instead (Tracker2 answers 502). Its pure helpers are tested in `tests/web/static/js/chart_grid/news.test.js`; Tracker2's `NewsPanel` and `SideColumn` cases need jsdom, so those are checked in the browser. `chart_grid.html` sets `main_pad` to `p-0` so the grid runs edge to edge, as in Tracker2.
- Not copied: Tracker2's analyzer page and modeler (`analyzer.html`, `modeler*.js`, `charts_panel.js`, `chart_lines.js`' strike lines, `analyzer_handoff.js`), and its Ideas and roll mode.
- `trade/chain_stats.js`: imports `../modeler/pricing.js` instead of `./modeler_math.js`, since the pricing module lives there since slice 1. Brought up to Tracker2's ac941c41 (#211): BP Eff. is signed (uses BP = positive), BP After and Fees added. `trade/chain_tray.js` gained ac941c41's `netPrices`.
- `trade/chain_panel.js` (from Tracker2's `analyzer/chain_panel.js`; listed in its header comment):
  - Mounts inline in an element it is given, not as an overlay.
  - The ticker comes from the page (`setTicker`) rather than its own box.
  - The tray goes to `onChange` instead of **Send to Analyzer**.
  - It reads `/api/trade/*` instead of Tracker2's `/api/analyzer/*`.
  - No account box, buying-power preview, working list or Send button (the ticket has those).
  - Pos replaced Vol as in Tracker2 (#210), but OI still shows only when the chain has it. The account names come from the page (`opts.accounts`), not the panel's own account box.
- `trade/chain_held.js` (Tracker2's `analyzer/chain_held.js`): a cell matches by expiration, strike and call/put only. Tracker2 also matched futures legs by stream symbol, which this app's journal legs don't carry. Its test, `tests/web/static/js/trade/chain_held.test.js`, drops that case.
