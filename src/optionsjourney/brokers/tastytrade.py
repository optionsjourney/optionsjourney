"""TastyTrade REST API: accounts, balances and transactions.

Talks to the REST API directly with httpx rather than through the tastytrade
SDK (see docs/decisions/0012-broker-apis-over-plain-http.md)."""

from datetime import date, datetime
from decimal import Decimal

import httpx

from optionsjourney.brokers.credentials import CredentialProvider
from optionsjourney.brokers.http import decimal, get_json, new_client, post_json, send_json
from optionsjourney.core.accounts import Balance, BrokerAccount, DailyValue
from optionsjourney.core.holdings import StockPosition
from optionsjourney.core.symbols import normalize_underlying, parse_futures_option, parse_occ
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    RawTransaction,
    Side,
)
from optionsjourney.trading.orders import BrokerOrder, plain
from optionsjourney.trading.tastytrade import broker_order

BROKER = "tastytrade"
PRODUCTION_URL = "https://api.tastyworks.com"
SANDBOX_URL = "https://api.cert.tastyworks.com"
PAGE_SIZE = 250


class TastyTradeClient:
    name = "TastyTrade"
    broker = BROKER

    def __init__(self, credentials: CredentialProvider, *, http: httpx.Client | None = None):
        self._credentials = credentials
        self._http = http or new_client()

    def accounts(self) -> list[BrokerAccount]:
        items = self._get("/customers/me/accounts")["data"].get("items", [])
        return [_account(item["account"]) for item in items if "account" in item]

    def balance(self, account_number: str) -> Balance:
        data = self._get(f"/accounts/{account_number}/balances")["data"]
        return Balance(
            net_liquidating_value=decimal(data.get("net-liquidating-value")),
            cash=decimal(data.get("cash-balance")),
            buying_power=decimal(data.get("derivative-buying-power")),
        )

    def balance_history(self, account_number: str, start: date, end: date) -> list[DailyValue]:
        """The account's end-of-day value for each day from `start` to `end`.
        TastyTrade keeps these; Schwab doesn't (docs/decisions/0023)."""
        items, offset = [], 0
        while True:
            body = self._get(
                f"/accounts/{account_number}/balance-snapshots",
                params={
                    "start-date": start.isoformat(),
                    "end-date": end.isoformat(),
                    "time-of-day": "EOD",
                    "page-offset": offset,
                },
            )
            items.extend(body["data"].get("items", []))
            offset += 1
            if offset >= int(body.get("pagination", {}).get("total-pages", 1)):
                break
        return [
            DailyValue(
                day=date.fromisoformat(item["snapshot-date"]),
                net_liquidating_value=decimal(item.get("net-liquidating-value")),
                cash=decimal(item.get("cash-balance")),
            )
            for item in items
            if item.get("snapshot-date")
        ]

    def quote_token(self) -> tuple[str, str]:
        """(token, websocket URL) for TastyTrade's DXLink quote stream."""
        data = self._get("/api-quote-tokens")["data"]
        return data["token"], data["dxlink-url"]

    def futures_option_streamer_symbols(self, root: str) -> dict[str, str]:
        """Each futures option of a product ("ES") by symbol -> its DXLink
        streamer symbol ("./ESZ6 EWX6  261130P7000" -> "./EWX26P7000:XCME")."""
        items = self._get(f"/futures-option-chains/{root}")["data"].get("items", [])
        return {i["symbol"]: i["streamer-symbol"] for i in items if i.get("streamer-symbol")}

    def preview_order(self, account_number: str, order: dict) -> dict:
        """TastyTrade's dry run of an order, never placed: its buying-power
        effect, fees and any warnings. Allowed even by the gateway guard."""
        return self._post(f"/accounts/{account_number}/orders/dry-run", order).get("data", {})

    def place_order(self, account_number: str, order: dict) -> str:
        """Send an order; its id. Only services/orders.py calls this, after
        the central check (decision 0028)."""
        data = self._post(f"/accounts/{account_number}/orders", order).get("data", {})
        return str((data.get("order") or {}).get("id") or "")

    def replace_order(self, account_number: str, order_id: str, order: dict) -> str:
        """Replace a working order in one step; the new order's id."""
        data = self._send("PUT", f"/accounts/{account_number}/orders/{order_id}", order)
        data = data.get("data", {})
        return str(data.get("order", data).get("id") or "")

    def cancel_order(self, account_number: str, order_id: str) -> None:
        self._send("DELETE", f"/accounts/{account_number}/orders/{order_id}")

    def get_order(self, account_number: str, order_id: str) -> BrokerOrder:
        return broker_order(self._get(f"/accounts/{account_number}/orders/{order_id}")["data"])

    def recent_orders(self, account_number: str, since=None) -> list[BrokerOrder]:
        """Today's orders and every order still working (TastyTrade's
        /orders/live). `since` is accepted for the same shape as Schwab's."""
        items = self._get(f"/accounts/{account_number}/orders/live")["data"].get("items", [])
        return [broker_order(item) for item in items]

    def working_orders(self, account_number: str) -> list[BrokerOrder]:
        return [o for o in self.recent_orders(account_number) if o.may_be_working]

    def option_positions(self, account_number: str) -> dict[str, int]:
        """Open option contracts by plain symbol: positive long, negative short."""
        items = self._get(f"/accounts/{account_number}/positions")["data"].get("items", [])
        held: dict[str, int] = {}
        for item in items:
            if item.get("instrument-type") not in ("Equity Option", "Future Option"):
                continue
            quantity = int(decimal(item.get("quantity") or "0"))
            if item.get("quantity-direction") == "Short":
                quantity = -quantity
            key = plain(item.get("symbol", ""))
            held[key] = held.get(key, 0) + quantity
        return held

    def equity_tick_sizes(self, symbol: str) -> list[dict]:
        """A stock's option increments: [{"threshold": "3.0", "value": "0.05"}, ...]."""
        return self._get(f"/instruments/equities/{symbol}")["data"].get("option-tick-sizes", [])

    def future_tick_sizes(self, contract: str) -> list[dict]:
        """A futures contract's spread increments ('/ESZ6'), else its option increments."""
        data = self._get(f"/instruments/futures/{contract.lstrip('/')}")["data"]
        return data.get("spread-tick-sizes") or data.get("option-tick-sizes") or []

    def futures_option_tick_sizes(self, product: str) -> dict[str, list[dict]]:
        """Each futures option series of a product ('ES') -> its increments."""
        data = self._get(f"/futures-option-chains/{product}/nested")["data"]
        return {
            expiration["option-contract-symbol"]: expiration.get("tick-sizes", [])
            for chain in data.get("option-chains", [])
            for expiration in chain.get("expirations", [])
            if expiration.get("option-contract-symbol")
        }

    def stock_positions(self, account_number: str) -> list[StockPosition]:
        """The shares held in an account, with TastyTrade's average open
        price and the previous close."""
        items = self._get(f"/accounts/{account_number}/positions")["data"].get("items", [])
        found = []
        for item in items:
            if item.get("instrument-type") != "Equity":
                continue
            quantity = decimal(item.get("quantity") or "0")
            if item.get("quantity-direction") == "Short":
                quantity = -quantity
            close = decimal(item.get("close-price") or "0")
            found.append(
                StockPosition(
                    f"{BROKER}:{account_number}",
                    item["symbol"],
                    quantity,
                    decimal(item.get("average-open-price") or "0"),
                    close or None,
                )
            )
        return found

    def futures(self, product_code: str) -> list[dict]:
        """A futures product's contracts ("ES"), each with "active-month" and
        its DXLink "streamer-symbol" ("/ESZ26:XCME")."""
        params = {"product-code[]": [product_code]}
        return self._get("/instruments/futures", params=params)["data"].get("items", [])

    def market_data(self, by_type: dict[str, list[str]]) -> list[dict]:
        """Snapshot quotes ({"index": [...], "equity": [...], "future": [...]}):
        last, mark, bid, ask, prev-close and more per symbol."""
        params = {kind: symbols for kind, symbols in by_type.items() if symbols}
        return self._get("/market-data/by-type", params=params)["data"].get("items", [])

    def market_metrics(self, symbols: list[str]) -> list[dict]:
        """Per symbol: IV rank, liquidity, and the next "earnings" report
        ({"expected-report-date", "time-of-day", "estimated", "visible"})."""
        params = {"symbols": ",".join(symbols)}
        return self._get("/market-metrics", params=params)["data"].get("items", [])

    def futures_option_nested(self, product_code: str) -> dict:
        """A futures product's option chain ("ES"): {"futures": [contracts with
        their streamer symbols], "option-chains": [{"expirations": [series with
        notional value, settlement type and strikes]}]}."""
        return self._get(f"/futures-option-chains/{product_code}/nested")["data"]

    def option_chain(self, symbol: str) -> list[dict]:
        """The nested option chain: one item per root (SPX has SPXW and SPX),
        each with expirations and their strikes' OCC symbols."""
        return self._get(f"/option-chains/{symbol}/nested")["data"].get("items", [])

    def transactions(self, account_number: str, start: date, end: date) -> list[RawTransaction]:
        """Every transaction from `start` to `end` (inclusive), all pages."""
        raw, offset = [], 0
        while True:
            body = self._get(
                f"/accounts/{account_number}/transactions",
                params={
                    "start-date": start.isoformat(),
                    "end-date": end.isoformat(),
                    "per-page": PAGE_SIZE,
                    "page-offset": offset,
                },
            )
            raw.extend(body["data"].get("items", []))
            offset += 1
            if offset >= int(body.get("pagination", {}).get("total-pages", 1)):
                break
        return [
            RawTransaction(
                transaction_id=str(item["id"]),
                executed_at=datetime.fromisoformat(item["executed-at"]),
                transaction_type=item.get("transaction-type", ""),
                data=item,
            )
            for item in raw
        ]

    def _post(self, path: str, body: dict) -> dict:
        token = self._credentials.tastytrade_token()
        base = SANDBOX_URL if token.is_test else PRODUCTION_URL
        return post_json(self._http, base + path, token.value, self.name, body)

    def _send(self, method: str, path: str, body: dict | None = None) -> dict:
        token = self._credentials.tastytrade_token()
        base = SANDBOX_URL if token.is_test else PRODUCTION_URL
        response = send_json(self._http, method, base + path, token.value, self.name, body)
        return response.json() if response.content else {}

    def _get(self, path: str, params: dict | None = None) -> dict:
        token = self._credentials.tastytrade_token()
        base = SANDBOX_URL if token.is_test else PRODUCTION_URL
        return get_json(self._http, base + path, token.value, self.name, params)


def _account(raw: dict) -> BrokerAccount:
    return BrokerAccount(
        broker=BROKER,
        number=raw["account-number"],
        nickname=raw.get("nickname") or None,
        account_type=raw.get("account-type-name") or None,
    )


# --- Raw transaction -> fills -------------------------------------------------

_RECEIVE_DELIVER_KINDS = {
    "expiration": Kind.EXPIRATION,
    "assignment": Kind.ASSIGNMENT,
    "cash settled assignment": Kind.ASSIGNMENT,
    "exercise": Kind.EXERCISE,
    "cash settled exercise": Kind.EXERCISE,
    "maturity": Kind.MATURITY,
}

_INSTRUMENT_TYPES = {
    "equity option": InstrumentType.OPTION,
    "future option": InstrumentType.FUTURE_OPTION,
    "equity": InstrumentType.STOCK,
    "future": InstrumentType.FUTURE,
}

_FEE_FIELDS = (
    "commission",
    "clearing-fees",
    "regulatory-fees",
    "proprietary-index-option-fees",
)


def parse_transaction(raw: dict, account_number: str) -> list[Fill]:
    """The fills in one TastyTrade transaction: one for a trade, expiration,
    assignment or exercise; none for money movements."""
    kind = _kind(raw)
    if kind is None or not raw.get("symbol"):
        return []

    instrument_type = _INSTRUMENT_TYPES.get(
        (raw.get("instrument-type") or "").lower(), InstrumentType.OTHER
    )
    symbol = raw["symbol"]
    underlying = normalize_underlying(raw.get("underlying-symbol") or symbol.split()[0])
    option = None
    if instrument_type is InstrumentType.OPTION:
        parsed = parse_occ(symbol)
        option = parsed[1] if parsed else None
    elif instrument_type is InstrumentType.FUTURE_OPTION:
        option = parse_futures_option(raw.get("description"), symbol)

    side, effect = _side_and_effect(raw.get("action"))
    price = raw.get("price")
    return [
        Fill(
            broker=BROKER,
            account_number=account_number,
            transaction_id=str(raw["id"]),
            leg=0,
            executed_at=datetime.fromisoformat(raw["executed-at"]),
            kind=kind,
            instrument_type=instrument_type,
            symbol=symbol,
            underlying=underlying,
            option=option,
            side=side,
            effect=effect,
            quantity=abs(decimal(raw.get("quantity"))),
            price=abs(decimal(price)) if price not in (None, "") else None,
            value=_signed(raw, "value"),
            fees=-sum((_signed(raw, field) for field in _FEE_FIELDS), Decimal("0")),
            order_id=str(raw["order-id"]) if raw.get("order-id") is not None else None,
            description=raw.get("description"),
        )
    ]


# Money moved into or out of an account, as opposed to income (dividends,
# interest) or trading.
_CASH_FLOW_SUB_TYPES = {"deposit", "withdrawal", "transfer"}


def cash_flow(raw: dict) -> Decimal | None:
    """Money moved in (+) or out (-) by this transaction, or None if it isn't
    a deposit, withdrawal or transfer."""
    if raw.get("transaction-type") != "Money Movement":
        return None
    if (raw.get("transaction-sub-type") or "").lower() not in _CASH_FLOW_SUB_TYPES:
        return None
    return _signed(raw, "value")


def _kind(raw: dict) -> Kind | None:
    transaction_type = raw.get("transaction-type")
    if transaction_type == "Trade":
        return Kind.TRADE
    if transaction_type == "Receive Deliver":
        sub_type = (raw.get("transaction-sub-type") or "").lower()
        return _RECEIVE_DELIVER_KINDS.get(sub_type, Kind.OTHER)
    return None


def _side_and_effect(action: str | None) -> tuple[Side | None, Effect | None]:
    action = (action or "").lower()
    side = None
    if action.startswith("buy"):
        side = Side.BUY
    elif action.startswith("sell"):
        side = Side.SELL
    effect = None
    if "to open" in action:
        effect = Effect.OPEN
    elif "to close" in action:
        effect = Effect.CLOSE
    return side, effect


def _signed(raw: dict, field: str) -> Decimal:
    """A TastyTrade amount with its "-effect" applied: + credit, - debit."""
    amount = abs(decimal(raw.get(field)))
    return -amount if raw.get(f"{field}-effect") == "Debit" else amount
