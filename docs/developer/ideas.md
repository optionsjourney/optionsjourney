# Trade Ideas and the Pending drawer

Issue #292, decision [0114](../decisions/0114-trade-ideas-and-pending-orders.md). Design: the spec. The order side (owners, and why only your own orders can be changed: decision [0115](../decisions/0115-outside-orders-read-only.md)) is in [orders.md](orders.md#who-owns-a-working-order).

## Ideas
- **Table `ideas`** (migration 0055): `id`, `ticker`, `label`, `note`, `cards` (JSON list of `{label, qty, entry_unit, locked, multiplier, legs}`), `spot_at_entry`, `created_at`, `final_pnl`, `final_at`. Status is computed.
- **`core/ideas.py`** (pure): `validate` (one card, 1 to MAX_LEGS legs, no futures or VIX options, whole-number quantity and ratios; refusals are `IdeaRefused` with the message shown to the user), `status`, `card_unit` (the card's unit price from leg mids), `settled_unit` (from official closes), `card_pnl`, `pnl_percent`.
- **`services/ideas.py`:** `create`, `listing`, `markets`, `edit`, `delete`, `retire`, `for_ticket` (an idea as the Trade page ticket) and `count`. Live legs are priced from the quote hub; expired legs from `expiry_prices.official_closes`, asked at most every 300 s per idea. Once every leg is expired with a final close, `final_pnl` and `final_at` are written once and the idea is never priced again. A close that isn't final is shown, never stored. Nothing raises for a broker or hub failure: the card just has no P/L.
- **Routes** (`web/routes/ideas.py`): `POST/GET /api/ideas`, `GET /api/ideas/markets`, `PATCH/DELETE /api/ideas/<id>`.
- **Retiring:** `/api/trade/confirm` and `/api/trade/bot-entry` (in `web/routes/trade.py`) take an `idea_id`. The idea is retired only when `orders.submit` returned ok with action `send` (or the bot hand-off was accepted). A failed retire is logged and never fails the trade; the response says `Removed from Ideas.` when it worked.
- **Trade it:** `/trade?idea=<id>` loads `for_ticket`; only a live idea is opened.
- **Browser:** `static/js/ideas/` has `model.js` (pure: formatting, rows, chips), `drawer.js` (the Journal drawer: polls the markets endpoint every 3 s while open and visible) and `save.js` (the Idea ☆ dialog on the ticket and the Trade it banner). Templates: `_ideas_drawer.html`. Tests: `tests/web/static/js/ideas/`.

## Pending
- **`core/pending.py`** (pure): `category` (0dte, calendars, options).
- **`services/pending_orders.py`:** `snapshot` reads every active account's working orders in parallel (10 s per account); a failing account has `error` set and the others are unaffected. `order_marks` / `listed_marks` price an order's legs as a signed mid. `/api/pending/markets` never calls a broker: it prices the legs remembered from the last snapshot (`_listed`, replaced on every snapshot), so a new order has no mid until the next list.
- **Routes** (`web/routes/pending.py`): `GET /api/pending`, `GET /api/pending/markets`. Change price and Cancel post to the Trade page's `/api/trade/replace` and `/api/trade/cancel`, so there is one rule in one place.
- **Browser:** `static/js/pending/` has `model.js` (pure: filters, chips, row shapes) and `drawer.js` (30 s list while open, 5 min count while closed, 2 s markets, "updating…" then a reload after an action; the filter is kept in `localStorage`). Template: `_pending_drawer.html`. Tests: `tests/web/static/js/pending/`.
- **Row shapes:** `rowView` also gives `legs` (`legView`: signed quantity, "Oct 24", strike, C/P and the BTO/STO/BTC/STC badge, from `trade/ticket_core.js`'s `optionParts`), `statusText`, `ownerKind` and the price split into number and side (`moneyParts`). `asOfText` turns the listing's `as_of` into the header's "as of 10:42" (New York).

## How the two drawers look (#294)
Both are plain CSS in `static/css/app.css` (the Journal loads no Tailwind): a gray-800 panel (`drawer__panel--surface`, `--wide` 1150 px for Pending, `--medium` 900 px for Ideas), a one-line header (`slim-drawer__*`: title, ⓘ, a muted status, ↻, ✕), Pending's filter bar (`pending-filters`, `chip-pill`), a compact table (`pending-table`, stacked cards under 768 px), the leg badges (`pending-badge--sell/--buy`) and small outlined buttons (`mini-btn--blue/--red/--gray`). Ideas are cards (`idea-card`, `idea-chip--<status>`, `idea-table`). The toolbar buttons are tinted pills (`toolbar-pill--amber`, red with `--warn` when an account failed, `--violet`).
