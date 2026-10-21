# Transactions and sync

How trades get from the brokers into the app.

```
broker API ──sync──▶ broker_transactions (raw JSON) ──parse──▶ Fill ──group──▶ OrderGroup ──▶ pages, positions (next)
```

## 1. Sync (`services/sync.py`)
- **What it does:** `sync_all` runs `sync_broker` for each broker client. For every account it:
  1. saves the account (nickname, type) to `accounts`
  2. works out the window: from the last sync **minus 7 days**, or **today** on the first sync, through today (Eastern time). Nothing starts before `history_starts` (a `settings` row, set to the first sync's day): transactions dated before it are dropped unless already stored, so history begins the day the app is first used (decision 0064)
  3. fetches the raw transactions and stores them: new ones are inserted, and ones whose JSON changed are replaced
  4. records the result in `sync_state`, including any error
- **Failures:** any error from one broker or account (not just a refusal) is recorded on that account's `sync_state.last_error` and never stops the others. A reply that isn't JSON is a `BrokerError`. `problem_text` shows a refusal's own words, or "{broker} sent a reply the app couldn't read." for anything unexpected (logged).
- **How it's triggered:** the **Sync now** button on the Transactions page (`POST /sync`), and the background sync (#171): `services/auto_sync.py` runs from `__main__` (never in tests), looks every 15 seconds, and syncs when `core/auto_sync.py` says one is due, from the settings `sync_market_hours` (default every minute, during the regular stock session) and `sync_other_times` (default hourly), each a number of minutes or `off`. `sync_all` holds a lock, so the two never run at once. Fills start a sync too (#172, `services/fill_sync.py`, setting `sync_on_fill`, default on), as Tracker2's `account_stream.py`: a trigger only. `FillSync` syncs `SETTLE` (3 s) after the last fill notice, at most once per `GAP` (10 s). `TastyAccountStream` listens on TastyTrade's account streamer (`wss://streamer.tastyworks.com`: `connect` with every account number and `Bearer` + the session token, a `heartbeat` every 20 s, rebuilt every 30 minutes, reconnecting after drops) in both modes; TastyTrade has no one-session limit (checked 2026-10-18 alongside Tracker2's). Schwab's account activity (`ACCT_ACTIVITY`) rides on the Schwab price stream while it's open, so local mode only (decision 0052); any message but `SUBSCRIBED` counts.

### Broker clients
Each client has `transactions(account_number, start, end) -> list[RawTransaction]`:

| Broker | Endpoint | Notes |
| --- | --- | --- |
| TastyTrade | `GET /accounts/{number}/transactions` | Reads every page, 250 per page |
| Schwab | `GET /trader/v1/accounts/{hash}/transactions` | 60-day windows; the end date is exclusive; all transaction types are requested |

## 2. Parse (`brokers/*/parse_transaction`)
- **What it does:** `parse_transaction(raw, account_number) -> list[Fill]` is a pure function, one per broker. It returns no fills for money movements (deposits, dividends, interest), one fill for a TastyTrade trade, and one fill per instrument for Schwab.
- **Where the code lives:** the knowledge of each broker's format stays in `brokers/`. What a `Fill` is lives in `core/transactions.py`.

### What's in a fill

| Field | Meaning |
| --- | --- |
| `kind` | `trade`, `expiration`, `assignment`, `exercise`, `maturity` (a Treasury bill or bond paid out), `other` |
| `instrument_type` | `option`, `future_option`, `stock`, `future`, `other` |
| `underlying` | Normalized: `SPXW` and `$SPX` both become `SPX` (`core/symbols.py`) |
| `option` | Put/call, strike and expiration, parsed from the OCC symbol (or, for futures options, from the description) |
| `side`, `effect` | Buy/sell, and open/close where the broker says so |
| `quantity`, `price` | Unsigned |
| `value` | Cash effect: **+ received, − paid**. Fees are not included. |
| `fees` | Total fees, as a positive cost |

### How each broker's fields map

| Fill | TastyTrade | Schwab |
| --- | --- | --- |
| side / effect | `action` ("Sell to Open") | sign of the item's `amount`; `positionEffect` |
| value | `value` with `value-effect` (Credit/Debit) | the item's `cost` (or price × quantity × multiplier) |
| fees | commission, clearing, regulatory and index-option fees, each with its effect | `transferItems` that have a `feeType`, all put on the first fill |
| kind | `transaction-type`, plus `transaction-sub-type` for Receive Deliver | `type`, plus the description for RECEIVE_AND_DELIVER |
| symbol | `symbol` | the instrument's `symbol`. Schwab leaves it out on some legs of contracts it has since disabled; it's then rebuilt from `optionDeliverables[0].rootSymbol`, expiration, put/call and strike (`occ_symbol`), since matching compares symbols |

## 3. Group into orders (`core/orders.py`, `core/structures.py`)
- **What it does:** `group_fills(fills) -> list[OrderGroup]` groups fills by `(broker, account, order ID, kind, effect)`, so opens and closes stay apart ([0016](../decisions/0016-group-fills-by-order.md)). Fills without an order ID (expirations, assignments, exercises) are grouped by account, kind, time and underlying.
- **Legs:** each group's **legs** are its instruments, with partial fills added together. They're expressed as the position they form (+ long, − short). For a close, that's the position being closed.
- **Names:** `describe(legs)` names the structure, and `OrderGroup.name` adds the underlying, e.g. "SPX Iron Condor".

## 4. Show (`services/transactions.py`, `web/routes/transactions.py`)
- **What it does:** `load_fills` reads the stored rows for a period (and optionally one account), parses them, and returns fills newest first.
- **Two views:** the page groups them into orders by default (`?view=orders`), or lists each fill (`?view=fills`).
- **Unreadable rows:** a row that fails to parse is logged and counted, and the page says how many weren't shown.

## Manual entries (`brokers/manual.py`, `services/manual.py`)
Trades entered by hand ([0023](../decisions/0023-manual-entry-backups-account-values.md)) are stored in `broker_transactions` under the broker `manual`, as JSON that `build_entry` writes (after checking every field) and `parse_transaction` reads back into fills. Options use the standard 100 multiplier; futures options aren't supported by hand. Manual accounts are `accounts` rows with broker `manual` and numbers `M001`, `M002`, … Only manual entries can be deleted. The pages are in `web/routes/manual.py`.

## Account values (`services/account_values.py`)
**Sync now** also records each account's end-of-day value:
- **TastyTrade:** `balance_history` reads `/accounts/{n}/balance-snapshots` (end of day). The first sync reaches back a year, and later ones re-read from 3 days before the last stored day.
- **Schwab:** it has no history, so today's balance is stored, and the last read of the day wins. The Accounts page records it too, since it reads balances anyway.

Deposits, withdrawals and transfers come from the stored transactions (`cash_flow` in each broker module). `core/account_values.py` adds the accounts up per day, carrying each one's last value over gaps, and works out the gain without money moved or accounts joining. The page is `/accounts/values`, drawn with `web/static/js/line_chart.js`.

## Live prices (`core/quotes.py`, `services/streaming.py`)
- **Symbol keys:** one per instrument across the app. Options use the OCC symbol (`SPXW  261016P05800000`, as fills carry it); stocks and indexes use the ticker (`SPY`, `SPX`). Each stream translates: DXLink wants `.SPXW261016P5800`, Schwab wants `$SPX` for indexes.
- **TastyStream:** DXLink. A quote token comes from `GET /api-quote-tokens`, then `SETUP` → `AUTH` → `CHANNEL_REQUEST` (FEED) → `FEED_SETUP` (COMPACT; Quote: bid/ask, Trade: price) → `FEED_SUBSCRIPTION`, with a `KEEPALIVE` every 30s.
- **SchwabStream:** the address comes from `GET /trader/v1/userPreference` (`streamerInfo`). It sends an ADMIN `LOGIN` with the access token, then `SUBS`/`ADD` on `LEVELONE_EQUITIES` (fields 0–3) and `LEVELONE_OPTIONS` (0, 2–4). Schwab sends only changed fields, so they're merged.
- **Futures options:** their key is TastyTrade's symbol (`./ESZ6 EWX6  261130P7000`). DXLink needs TastyTrade's streamer symbol (`./EWX26P7000:XCME`), which comes from `GET /futures-option-chains/{ES}`, fetched once per product. Their dollars per point come from their own fills: cash ÷ (price × quantity), in `core/live.multipliers_from_fills`. Schwab's stream isn't asked for them.
- **QuoteHub:** subscribes **both** streams, so the backup is warm, and answers with TastyTrade's quote while its stream is up and the quote isn't more than 10 seconds (`STALE_GAP`) older than Schwab's, otherwise Schwab's (decisions 0021, 0080). In gateway mode it has **no Schwab stream** (`make_hub(..., gateway=True)`, decision 0052): Schwab allows one streamer session per login, and that login is the server's.
- **Threads:** streams run in background threads that start on the first subscription. They reconnect after 5, 10, 30, then 60s, and every 2 minutes when the broker isn't connected. That case is logged once, not on every check.
- **Pages:** they ask `GET /api/quotes?symbols=…`. Settings → Brokers shows each stream's state.

## Charts (`core/candles.py`, `services/candles.py`, `services/charts.py`)
- **History:** TastyTrade's DXLink candles come first, as a one-off snapshot on their own connection. The symbol is `SPY{=5m}`, `SPX{=h,a=s}` for hours aligned to 09:30, or `SPY{=1d}`; the request is `FEED_SUBSCRIPTION` with `fromTime`, and bars are read until the `SNAPSHOT_END`/`SNIP` flag. Schwab's `/marketdata/v1/pricehistory` is the backup, with hours built from 30-minute bars. The reach is 4 days for 1m up to 760 days for 1d. Intraday bars are kept to 09:30–16:00 New York, except futures. Fetches are cached 10s (5 minutes for daily).
- **Daily bar stamps:** TastyTrade puts daily bars at 00:00 UTC of their date, Schwab at 05:00 UTC, so their date is read in UTC.
- **`/api/candles`:** bars, EMA 20/50, VWAP (restarting daily), the last price (the live quote moves the forming bar), and the change against the previous daily close.
- **The grid:** `/api/chart-layout` keeps it as an internal settings row. `web/static/js/charts.js` draws SVG candlesticks, scroll to zoom, drag to pan, with a crosshair, and refreshes every 10s. `/charts/pane` is the pop-out.

## Alerts (`core/alerts.py`, `services/alerts.py`)
User alerts (#27) are stored in `user_alerts` (price or a trade's open P/L, above/below a level, one-shot). `services/user_alerts.check` runs every 15s in a background thread started from `__main__` and the menu bar app, using the quote hub's marks and `journal.open_pl_of`. Alerts that went off join the system alerts until dismissed, and are passed to `services/notify.send` (#28).

System alerts are worked out on every page from the sync state and the last scheduled backup. They're never stored, so they clear themselves once the cause is fixed. A context processor puts the count in the top bar, and `/alerts` lists them, problems first. Price and trade alerts (#27) will be stored ones, added later.

## Request safety
`web/security.py` refuses any POST/PUT/PATCH/DELETE that doesn't come from the app's own pages (checked with the `Origin` / `Referer` header), so another website open in the same browser can't trigger actions.

## After a sync: the journal reloads and trades pop up
`web/live.py:watch_journal` listens on the session factory: a commit that added, changed or removed a `BrokerTransaction` or an `ExpirySettlement` pushes Socket.IO `journal_changed`. The Journal page (`journal_live.js`) reloads 1.5 s later, or shows a Reload link while trades are ticked, a drawer or menu is open or a field has focus.

The same commit starts `services/trade_events.record` on a thread (not in tests; #178, Tracker2's transaction alerts). It loads the journal (with the app's positions cache), turns each trade's events into alerts with `core/trade_events.events_of` (open, roll, `partial_close`, close; expirations and assignments aren't alerts; a trade's last close is `close` once nothing is open), and stores the ones from the last day not stored yet in `trade_events` (unique `dedupe_key`: kind, position key, order ids). A close also carries `detail` (#212, Tracker2's trade-closed alert, from `core/trade_events.close_detail`): "Strikes: …" of the live legs (each leg's strike in order, rolled-away legs left out), then "Rolled: YYYY-MM-DD old → new" per `ROLL` event (its New York day; old is the strikes of the roll's closing order, new those of its opening orders). The popup and the Alerts page show it one line each. Each new one is pushed as `trade_event`, with `popup` true only when the fill is under 10 minutes old and the `trade_popups` setting is on. Every page (`base.html`, `tracker_base.html`) loads `trade_toasts.js`, which shows the popups and re-sends each event as a window `trade-event`, which the Alerts page's Trades list adds live. A trade's name and bot are kept as they were when it was recorded; switching a condor between split and combined later records its halves again.
