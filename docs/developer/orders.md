# Orders

Phase 6, piece 1 (#31): one road to a broker for every order. Bots, managed trades and buttons all use it. The design is in [0028](../decisions/0028-one-order-path-and-two-pause-switches.md) and the spec.

## The path
`services/orders.py` holds the only code that sends, replaces or cancels an order: `submit()`, `replace()` and `cancel()`. In order:
0. **Freeze first:** if Freeze all orders is on, the order is refused and logged before anything is read from the broker.
1. **Clear up earlier unclear outcomes** on the account, if there are any.
2. **Gather the facts:** the two switches, tick sizes, the bot's limits, and for a closing order what's open and what working orders already cover.
3. **Run `trading/check.py`.** A refusal stops here.
4. **Ask the broker's preview** (TastyTrade dry run, Schwab `previewOrder`). A preview ends here with the broker's summary. A live order whose preview fails isn't sent.
5. **Send once.** A failed send is never retried. An unclear one is logged as `Unclear: …`, blocks its legs and raises an alert.

Every attempt is logged in `order_log`. Broker error text can quote the account number, so each message is passed through `core.accounts.mask_number` (the number cut to its last four) before it is logged, returned or alerted; the trading guard's own refusal leaves the account segment out of the path it quotes.

**The contract: `submit()`, `replace()` and `cancel()` never raise; every outcome is a logged `Result`.** If a fact can't be gathered for any reason (a broker answer that can't be read, a missing bot, a rounding or formatting error), the order is refused and logged; for a closing order the result starts with "Couldn't check" and the "A closing order wasn't sent" alert goes out. A preview that can't be read counts as a failed preview. Only the send itself has its own handling: an answer that can't be read there is unclear, not a refusal.

**One lock per account.** The app serves requests on threads, so two orders on one account (a double click, or a stop and a Close button) could otherwise read the same position and both pass. A lock per (broker, account) is held from step 1 until the log row is written, for orders and cancels alike.

**Recent own sends count as covering.** Right after a send, the broker's working list can lag. So for rule 4, this app's own successful closing sends and replaces from the last 5 minutes on the account also count as covering their contracts, unless the broker's working list already shows them (then they count once, from the list). An order that was later replaced, or that this replace is replacing, doesn't count. An order the broker reports in a status the app doesn't know counts as working, so the check fails closed.

## The pieces
| Where | What |
|---|---|
| `trading/orders.py` | The neutral order: legs (OCC or futures symbol, side, open/close, ratio), a quantity, one signed price (positive is a credit, negative a debit), order type, time in force. Refuses invalid combinations: a symbol that isn't a padded OCC symbol (an unpadded one is padded) or a three-part futures option, a count that isn't a whole number, a price that isn't a finite number, and a single leg whose price sign doesn't match its side. Also the broker's order read back (`BrokerOrder`) and the shared status. Pure. |
| `trading/ticks.py` | Tick sizes and rounding. Pennies by default; nickels for SPX/SPXW, RUT/RUTW, NDX/NDXP; per-stock and futures tables passed in. A credit rounds down, a debit up. Pure. |
| `trading/check.py` | The central check. Pure. |
| `trading/tastytrade.py` | Neutral order to TastyTrade's body (the app's tag goes in `external-identifier`); TastyTrade's order back to the shared form. Pure. |
| `trading/schwab.py` | The same for Schwab. Pure. |
| `brokers/tastytrade.py` | `preview_order` (dry run), `place_order`, `replace_order`, `cancel_order`, `get_order`, `recent_orders`, `working_orders`, and tick sizes: `equity_tick_sizes`, `future_tick_sizes`, `futures_option_tick_sizes`. |
| `brokers/schwab.py` | `preview_order`, `place_order`, `replace_order`, `cancel_order`, `get_order`, `recent_orders`, `working_orders`. |
| `services/orders.py` | The path above, plus the switches, tick tables and clearing up unclear outcomes. |
| `web/routes/orders.py` | The switches, the Freeze confirmation and the order log page. |

`trading/` imports only `core`. `brokers` may import `trading` to read orders back.

## The check's rules
`check()` answers send, preview or refuse, with a reason in plain words. Rules, in order:
1. **Freeze all orders** refuses everything, previews and cancels included.
2. **A valid order:**
   - futures go through TastyTrade only
   - overnight (GTC overnight) is for Cboe index options at TastyTrade only
   - the price and any trigger are on tick
   - no earlier unclear order on its legs
   - a bot never sends a multi-leg order at market (Tracker2's `exit_chase` rule 1; bots slice 3). Your own orders aren't held to it, and a bot's single leg may go at market.
3. **Opening orders:**
   - not while **No new trades** is on
   - only a bot may open
   - no market orders
   - within the bot's per-trade and per-day limits (unclear sends count)
4. **Closing orders:** each leg closes something open on the opposite side, for no more than what's open minus what working closing orders already cover. If the positions or working orders can't be read, the order is refused ("Couldn't check…") and an alert goes out.
5. **Preview or live:** a bot follows its own preview setting. A managed trade follows its own (piece 3). A button you click is live.

**Cancels** pass rule 1 only (`check_cancel`). **Replaces** are checked as the order they would become, leaving the replaced order out of the working count.

## Unclear outcomes
The app never guesses whether an order reached the broker.
- **What's unclear:** a timeout, a dropped connection or a 5xx after a send, and any broker answer that can't be read. A cancel can be unclear too: it's logged and alerted the same way, but it doesn't block any legs, because the broker's live working list (read for every closing order) already shows whether the order is still working.
- **The log:** the row's result starts with `Unclear:` (`services.orders.UNCLEAR`). It counts toward a bot's daily limit as if sent. An alert goes out.
- **The block:** while a send or replace is unclear and not cleared, the check refuses any order on the same account and the same legs. Older rows whose order the app can't read back (carried over from `bot_orders`) don't block and aren't matched; you clear them by hand.
- **How it clears** (`cleared_at` is set, and a note is added to the result):
  1. **Found:** the order is in the broker's list, so the row becomes `ok` with the broker's order id.
  2. **Not there:** the broker doesn't show it a minute or more later. This is only recorded when the attempt and the check are on the same New York day, because the broker's list covers today. Later than that the row stays unclear until it is found or you clear it.
  3. **You clear it** on the order log page.

  A cancel clears once the broker says how the order stands.
- **When it looks:** `resolve_unclear()` runs at the start of every order on the account.
- **Matching:**
  - TastyTrade: the app's tag (`external-identifier`), saved in the row's `tag`.
  - Schwab takes no custom tag. It stamps each order with a tag for the app that placed it. The app learns it from the first order it places in each account (after that send is logged) and keeps it as `_schwab_tag:{account}`. Matching uses that tag when known, plus legs, quantity and time (2 minutes before to 5 minutes after the attempt).
  - One broker order can't confirm two log rows. An order that another row already owns, or that a replace is replacing, is skipped.

## Tables (migration 0014)
- **`order_log`:** one row per attempt: previews, sends, cancels, replaces and refusals. Replaces `bot_orders`; its rows carried over.
  - `broker`, `account_number`, `origin` (`bot`, `managed` or `you`), `bot_id`
  - `action`: `preview`, `send`, `cancel`, `replace` or `refuse`
  - `opening`, `quantity`, `price`, `order_type`
  - `order`: the neutral order and the broker's body
  - `result` and `ok`: what happened, in words
  - `broker_order_id`, `replaces_id`, `tag`, `cleared_at`
- **`tick_sizes`:** a product key, its increments as JSON, and when they were fetched. TastyTrade's tables are fetched when missing or a week old. If a fetch fails, a stored table (even an old one) is still used, else the default tick, and a wrong one shows as the broker's rejection. A key whose fetch failed isn't asked for again for 5 minutes (`TICKS_RETRY_AFTER`, remembered in memory by `_tick_failed`), so a TastyTrade that's down doesn't slow every entry and chase step. Schwab has no tick-size endpoint, so there is no fallback to it; a stock with no stored table still refuses to enter. Only a `BrokerError` or `CredentialsUnavailable` is absorbed; any other error still refuses the order.
- **Settings:** `_no_new_trades` and `_freeze` (`on` or `off`). The migration renamed `_bots_stopped` to `_freeze`, so anyone fully stopped stays fully stopped. If a switch can't be read, the app acts as if Freeze is on. `_schwab_tag:{account}` holds the Schwab app tag.

## Pages
- **The nav menu** (the three dots in the top bar): No new trades, Freeze (opens the confirmation at `/orders/freeze`) and a link to the order log.
- **The banners** (`_trading_banners.html`) show on every page while a switch is on, each with a link to the order log. Settings links to it too (in the Brokers section), and so does every order alert.
- **Order log** (`/orders`): newest first, the latest 200 rows (`PAGE_SIZE`), filterable by account, origin and action, with **Clear** on unclear rows.

## Gateway mode
The trading guard sits underneath, unchanged: place, replace and cancel are refused at the HTTP layer, while previews pass ([broker-credentials.md](broker-credentials.md)).

## Not yet
- Following an order until it fills (piece 3).
- Bot runs and linking fills to bots (piece 2).
- A manual order ticket for opening trades (not part of Phase 6).
