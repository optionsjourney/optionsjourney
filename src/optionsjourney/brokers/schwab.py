"""Schwab Trader API: accounts, balances and transactions.

Schwab addresses accounts by an opaque hash rather than the account number,
so accounts() learns the number -> hash mapping the other calls need."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import httpx

from optionsjourney.brokers.credentials import CredentialProvider
from optionsjourney.brokers.http import (
    SLOW_TIMEOUT_S,
    BrokerError,
    decimal,
    get_json,
    new_client,
    send_json,
)
from optionsjourney.core.accounts import INDEXES, Balance, BrokerAccount, DailyClose
from optionsjourney.core.holdings import StockPosition
from optionsjourney.core.market import market_date
from optionsjourney.core.symbols import normalize_underlying, occ_symbol, parse_occ
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    RawTransaction,
    Side,
)
from optionsjourney.trading.orders import BrokerOrder, plain
from optionsjourney.trading.schwab import broker_order

BROKER = "schwab"
BASE_URL = "https://api.schwabapi.com/trader/v1"
MARKET_DATA_URL = "https://api.schwabapi.com/marketdata/v1"
_SHARE_TYPES = {"EQUITY", "COLLECTIVE_INVESTMENT"}  # stocks, and ETFs
# Schwab caps a transactions request at one year; smaller windows also keep
# each response well under its result limit.
WINDOW = timedelta(days=60)
# Every transaction type the API accepts (it requires the list).
TRANSACTION_TYPES = (
    "TRADE,RECEIVE_AND_DELIVER,DIVIDEND_OR_INTEREST,ACH_RECEIPT,ACH_DISBURSEMENT,"
    "CASH_RECEIPT,CASH_DISBURSEMENT,ELECTRONIC_FUND,WIRE_OUT,WIRE_IN,JOURNAL,"
    "MEMORANDUM,MARGIN_CALL,MONEY_MARKET,SMA_ADJUSTMENT"
)


class SchwabClient:
    name = "Schwab"
    broker = BROKER

    def __init__(self, credentials: CredentialProvider, *, http: httpx.Client | None = None):
        self._credentials = credentials
        self._http = http or new_client()
        self._hashes: dict[str, str] = {}
        # Balances from the last accounts() answer, each used once by balance()
        self._fresh: dict[str, dict] = {}

    def accounts(self) -> list[BrokerAccount]:
        self._hashes = {
            item["accountNumber"]: item["hashValue"]
            for item in self._get("/accounts/accountNumbers")
        }
        details = [
            a
            for a in (item.get("securitiesAccount", {}) for item in self._get("/accounts"))
            if "accountNumber" in a
        ]
        types = {a["accountNumber"]: a.get("type") for a in details}
        self._fresh = {
            a["accountNumber"]: a["currentBalances"] for a in details if "currentBalances" in a
        }
        return [
            BrokerAccount(
                broker=BROKER,
                number=number,
                account_type=types[number].title() if types.get(number) else None,
            )
            for number in self._hashes
        ]

    def balance(self, account_number: str) -> Balance:
        balances = self._fresh.pop(account_number, None)
        if balances is None:
            balances = (
                self._get(f"/accounts/{self._hash(account_number)}")
                .get("securitiesAccount", {})
                .get("currentBalances", {})
            )
        # Margin accounts report liquidationValue/cashBalance; cash accounts
        # can leave those empty and report totalCash instead.
        return Balance(
            net_liquidating_value=decimal(
                balances.get("liquidationValue") or balances.get("totalCash")
            ),
            cash=decimal(balances.get("cashBalance") or balances.get("totalCash")),
            buying_power=decimal(balances.get("buyingPower")),
        )

    def transactions(self, account_number: str, start: date, end: date) -> list[RawTransaction]:
        """Every transaction from `start` to `end` (inclusive), in windows."""
        account_hash = self._hash(account_number)
        raw = []
        window_start = start
        while window_start <= end:
            window_end = min(window_start + WINDOW, end)
            raw.extend(
                self._get(
                    f"/accounts/{account_hash}/transactions",
                    params={
                        "startDate": _timestamp(window_start),
                        # The end is exclusive: run to midnight after window_end.
                        "endDate": _timestamp(window_end + timedelta(days=1)),
                        "types": TRANSACTION_TYPES,
                    },
                )
            )
            window_start = window_end + timedelta(days=1)
        return [
            RawTransaction(
                transaction_id=str(item["activityId"]),
                executed_at=datetime.fromisoformat(item["time"]),
                transaction_type=item.get("type", ""),
                data=item,
            )
            for item in raw
        ]

    def stock_positions(self) -> list[StockPosition]:
        """The shares (stocks and ETFs) held in every account, with Schwab's
        average price."""
        found = []
        for account in self._get("/accounts", params={"fields": "positions"}):
            details = account.get("securitiesAccount", {})
            number = details.get("accountNumber", "")
            for item in details.get("positions", []):
                instrument = item.get("instrument", {})
                if instrument.get("assetType") not in _SHARE_TYPES:
                    continue
                long = decimal(str(item.get("longQuantity") or 0))
                short = decimal(str(item.get("shortQuantity") or 0))
                basis = item.get("averagePrice") if long else item.get("averageShortPrice")
                found.append(
                    StockPosition(
                        f"{BROKER}:{number}",
                        instrument.get("symbol", ""),
                        long - short,
                        decimal(str(basis or 0)),
                    )
                )
        return found

    def streamer_info(self) -> dict:
        """Where and how to log in to Schwab's streamer: socket URL, customer
        and correlation ids, channel and function id."""
        info = self._get("/userPreference").get("streamerInfo") or [{}]
        return info[0]

    def option_chain(self, symbol: str, expiration: date, strike_count: int) -> dict:
        """Schwab's chain for one expiration: callExpDateMap / putExpDateMap,
        "YYYY-MM-DD:dte" -> strike -> [contract]."""
        token = self._credentials.schwab_token()
        return get_json(
            self._http,
            MARKET_DATA_URL + "/chains",
            token.value,
            self.name,
            {
                "symbol": f"${symbol}" if symbol in INDEXES else symbol,
                "contractType": "ALL",
                "strikeCount": strike_count,
                "fromDate": expiration.isoformat(),
                "toDate": expiration.isoformat(),
            },
        )

    def option_expirations(self, symbol: str) -> list[date]:
        token = self._credentials.schwab_token()
        body = get_json(
            self._http,
            MARKET_DATA_URL + "/expirationchain",
            token.value,
            self.name,
            {"symbol": f"${symbol}" if symbol in INDEXES else symbol},
        )
        return sorted(
            {date.fromisoformat(e["expirationDate"][:10]) for e in body.get("expirationList", [])}
        )

    def price_history(self, symbol: str, params: dict) -> list[dict]:
        """Raw candles from /marketdata/v1/pricehistory ({datetime ms, open,
        high, low, close, volume}). Indexes are $-prefixed here."""
        token = self._credentials.schwab_token()
        body = get_json(
            self._http,
            MARKET_DATA_URL + "/pricehistory",
            token.value,
            self.name,
            {"symbol": f"${symbol}" if symbol in INDEXES else symbol, **params},
        )
        return body.get("candles", [])

    def quotes(self, symbols: list[str]) -> dict:
        """Raw /marketdata/v1/quotes: Schwab symbol -> {"quote": {lastPrice,
        mark, closePrice, netChange, ...}, "regular": {...}, "reference": ...}.
        Pass Schwab's own symbols ("$SPX", "/ES")."""
        token = self._credentials.schwab_token()
        return get_json(
            self._http,
            MARKET_DATA_URL + "/quotes",
            token.value,
            self.name,
            {"symbols": ",".join(symbols), "fields": "quote,regular,reference"},
        )

    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        """Daily closing prices from `start` to `end`. Schwab keeps years of
        these, so any past expiration can be priced."""
        token = self._credentials.schwab_token()
        body = get_json(
            self._http,
            MARKET_DATA_URL + "/pricehistory",
            token.value,
            self.name,
            {
                "symbol": f"${symbol}" if symbol in INDEXES else symbol,
                "periodType": "month",
                "frequencyType": "daily",
                "frequency": 1,
                "startDate": int(datetime.combine(start, time(), UTC).timestamp() * 1000),
                "endDate": int(datetime.combine(end, time(23, 59), UTC).timestamp() * 1000),
            },
        )
        return [
            DailyClose(
                symbol,
                market_date(datetime.fromtimestamp(candle["datetime"] / 1000, UTC)),
                decimal(candle["close"]),
            )
            for candle in body.get("candles", [])
            if candle.get("close") is not None
        ]

    WORKING_LOOKBACK = timedelta(days=180)  # Schwab's GTC orders last up to 180 days

    def preview_order(self, account_number: str, order: dict) -> dict:
        """Schwab's check of an order, never placed. Allowed by the gateway guard."""
        response = self._send("POST", f"/accounts/{self._hash(account_number)}/previewOrder", order)
        body = response.json() if response.content else {}
        rejects = (body.get("orderValidationResult") or {}).get("rejects") or []
        if rejects:
            reasons = "; ".join(
                str(r.get("message") or r.get("activityMessage") or "") for r in rejects
            )
            raise BrokerError(f"Schwab refused it: {reasons}", detail=body)
        return body

    def place_order(self, account_number: str, order: dict) -> str:
        """Send an order; its id (from the Location header), or "" if Schwab
        didn't say. Only services/orders.py calls this."""
        return _order_id(
            self._send("POST", f"/accounts/{self._hash(account_number)}/orders", order)
        )

    def replace_order(self, account_number: str, order_id: str, order: dict) -> str:
        path = f"/accounts/{self._hash(account_number)}/orders/{order_id}"
        return _order_id(self._send("PUT", path, order))

    def cancel_order(self, account_number: str, order_id: str) -> None:
        self._send("DELETE", f"/accounts/{self._hash(account_number)}/orders/{order_id}")

    def get_order(self, account_number: str, order_id: str) -> BrokerOrder:
        return broker_order(self._get(f"/accounts/{self._hash(account_number)}/orders/{order_id}"))

    def recent_orders(self, account_number: str, since: datetime) -> list[BrokerOrder]:
        params = {
            "fromEnteredTime": _iso(since),
            "toEnteredTime": _iso(datetime.now(UTC) + timedelta(days=1)),
        }
        items = self._get(
            f"/accounts/{self._hash(account_number)}/orders", params=params, timeout=SLOW_TIMEOUT_S
        )
        return [broker_order(item) for item in items]

    def working_orders(self, account_number: str) -> list[BrokerOrder]:
        since = datetime.now(UTC) - self.WORKING_LOOKBACK
        return [o for o in self.recent_orders(account_number, since) if o.may_be_working]

    def option_positions(self, account_number: str) -> dict[str, int]:
        """Open option contracts by plain symbol: positive long, negative short."""
        data = self._get(f"/accounts/{self._hash(account_number)}", params={"fields": "positions"})
        held: dict[str, int] = {}
        for item in (data.get("securitiesAccount") or {}).get("positions", []):
            instrument = item.get("instrument", {})
            if instrument.get("assetType") != "OPTION":
                continue
            quantity = int(decimal(str(item.get("longQuantity") or 0))) - int(
                decimal(str(item.get("shortQuantity") or 0))
            )
            key = plain(instrument.get("symbol", ""))
            held[key] = held.get(key, 0) + quantity
        return held

    def _send(self, method: str, path: str, body: dict | None = None):
        token = self._credentials.schwab_token()
        return send_json(self._http, method, BASE_URL + path, token.value, self.name, body)

    def _hash(self, account_number: str) -> str:
        if account_number not in self._hashes:
            self.accounts()
        account_hash = self._hashes.get(account_number)
        if account_hash is None:
            raise BrokerError(f"Schwab doesn't list account {account_number[-4:]}.")
        return account_hash

    def _get(self, path: str, params: dict | None = None, timeout: float | None = None):
        token = self._credentials.schwab_token()
        return get_json(self._http, BASE_URL + path, token.value, self.name, params, timeout)


def _timestamp(day: date) -> str:
    return datetime.combine(day, time()).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _order_id(response) -> str:
    location = response.headers.get("Location", "")
    return location.rstrip("/").rsplit("/", 1)[-1] if location else ""


# --- Raw transaction -> fills -------------------------------------------------

_ASSET_TYPES = {
    "OPTION": InstrumentType.OPTION,
    "EQUITY": InstrumentType.STOCK,
    "COLLECTIVE_INVESTMENT": InstrumentType.STOCK,
    "FUTURE": InstrumentType.FUTURE,
}


def parse_transaction(raw: dict, account_number: str) -> list[Fill]:
    """The fills in one Schwab transaction: one per instrument it moved. Fees
    are listed separately in the transaction and go on its first fill."""
    kind = _kind(raw)
    if kind is None:
        return []

    items = raw.get("transferItems", [])
    legs = [
        item
        for item in items
        if item.get("instrument", {}).get("assetType") not in (None, "CURRENCY")
    ]
    fees = sum(
        (abs(decimal(item.get("amount"))) for item in items if item.get("feeType")),
        Decimal("0"),
    )
    executed_at = datetime.fromisoformat(raw["time"])
    order_id = str(raw["orderId"]) if raw.get("orderId") is not None else None

    fills = []
    for leg, item in enumerate(legs):
        instrument = item["instrument"]
        symbol = instrument.get("symbol", "")
        instrument_type = _ASSET_TYPES.get(instrument.get("assetType"), InstrumentType.OTHER)
        underlying = normalize_underlying(
            instrument.get("underlyingSymbol") or (symbol.split()[0] if symbol else "")
        )
        if instrument_type is InstrumentType.OPTION and underlying.startswith("/"):
            instrument_type = InstrumentType.FUTURE_OPTION
        option = _option(instrument) if instrument_type is InstrumentType.OPTION else None
        if option is not None and not symbol:
            # Schwab leaves the symbol out on some legs of disabled contracts;
            # matching needs it, so rebuild it from the details.
            symbol = occ_symbol(_option_root(instrument), option)

        amount = decimal(item.get("amount"))
        quantity = abs(amount)
        price = abs(decimal(item.get("price")))
        side = Side.SELL if amount < 0 else Side.BUY
        if "cost" in item:
            value = decimal(item["cost"])
        else:
            multiplier = decimal(instrument.get("optionPremiumMultiplier") or 1)
            value = price * quantity * multiplier * (1 if side is Side.SELL else -1)

        fills.append(
            Fill(
                broker=BROKER,
                account_number=account_number,
                transaction_id=str(raw["activityId"]),
                leg=leg,
                executed_at=executed_at,
                kind=kind,
                instrument_type=instrument_type,
                symbol=symbol,
                underlying=underlying,
                option=option,
                side=side,
                effect=_effect(item.get("positionEffect")),
                quantity=quantity,
                price=price,
                value=value,
                fees=fees if leg == 0 else Decimal("0"),
                order_id=order_id,
                description=raw.get("description"),
            )
        )
    return fills


# Money moved into or out of an account, as opposed to income or trading.
CASH_FLOW_TYPES = {
    "ACH_RECEIPT",
    "ACH_DISBURSEMENT",
    "CASH_RECEIPT",
    "CASH_DISBURSEMENT",
    "WIRE_IN",
    "WIRE_OUT",
    "ELECTRONIC_FUND",
    "INTERNAL_TRANSFER",
}


def cash_flow(raw: dict) -> Decimal | None:
    """Money moved in (+) or out (-) by this transaction, or None if it isn't
    a deposit, withdrawal or transfer. Schwab's netAmount is already signed."""
    if raw.get("type") not in CASH_FLOW_TYPES:
        return None
    return decimal(raw.get("netAmount"))


def _kind(raw: dict) -> Kind | None:
    transaction_type = raw.get("type")
    if transaction_type == "TRADE":
        return Kind.TRADE
    if transaction_type == "RECEIVE_AND_DELIVER":
        description = (raw.get("description") or "").upper()
        if "EXPIR" in description:
            return Kind.EXPIRATION
        if "ASSIGN" in description:
            return Kind.ASSIGNMENT
        if "EXERC" in description:
            return Kind.EXERCISE
        return Kind.OTHER
    return None


def _option(instrument: dict) -> OptionDetails | None:
    """From the OCC symbol when possible: it's exact, while expirationDate is a
    UTC timestamp."""
    parsed = parse_occ(instrument.get("symbol", ""))
    if parsed:
        return parsed[1]
    if (
        instrument.get("putCall")
        and instrument.get("strikePrice")
        and instrument.get("expirationDate")
    ):
        return OptionDetails(
            put_call=instrument["putCall"].upper(),
            strike=decimal(instrument["strikePrice"]),
            expiration=date.fromisoformat(instrument["expirationDate"][:10]),
        )
    return None


def _option_root(instrument: dict) -> str:
    deliverables = instrument.get("optionDeliverables") or [{}]
    return deliverables[0].get("rootSymbol") or instrument.get("underlyingSymbol") or ""


def _effect(position_effect: str | None) -> Effect | None:
    return {"OPENING": Effect.OPEN, "CLOSING": Effect.CLOSE}.get((position_effect or "").upper())
