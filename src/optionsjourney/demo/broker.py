"""The pretend brokers: a TastyTrade and a Schwab that trade against the made-up market.

Drop-in for ``brokers.tastytrade.TastyTradeClient`` and ``brokers.schwab.SchwabClient``
as the app's code paths use them: the same method names, arguments and return types,
and the same raw shapes (TastyTrade order and transaction JSON). Nothing here talks to
the network. Working orders fill in ``PretendTasty.match``, which the demo engine calls
with every tick the market moved through, so an order crossed during a Skip ahead fills
at the moment it was crossed.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import Balance, BrokerAccount, DailyClose, DailyValue
from optionsjourney.core.holdings import StockPosition
from optionsjourney.core.market import market_date
from optionsjourney.core.symbols import occ_symbol, parse_occ
from optionsjourney.core.transactions import OptionDetails, RawTransaction
from optionsjourney.demo.book import (
    CENT,
    LIVE,
    MULTIPLIER,
    SCHWAB_ACCOUNT,
    TASTY_ACCOUNT,
    Book,
    leg_prices,
    max_loss,
    option_fees,
)
from optionsjourney.demo.clock import CLOSE, DEMO_DAY, NY
from optionsjourney.demo.levels import VIX_BASE, VOL
from optionsjourney.demo.market import PretendMarket, Tick
from optionsjourney.trading.orders import BrokerOrder, plain
from optionsjourney.trading.tastytrade import broker_order

__all__ = ["SCHWAB_ACCOUNT", "TASTY_ACCOUNT", "Book", "PretendSchwab", "PretendTasty"]

# The chains: root -> (underlying, strike step, has a 0DTE on the demo day)
_CHAINS = {
    "SPX": ("SPXW", 5, True),
    "SPXW": ("SPXW", 5, True),
    "SPY": ("SPY", 1, False),
    "QQQ": ("QQQ", 1, False),
    "IWM": ("IWM", 1, False),
}
_WEEKLIES = 5  # Fridays listed after the demo day
_STRIKE_RANGE = Decimal("0.10")
_INDEX_TICKS = [{"threshold": "3.0", "value": "0.05"}, {"value": "0.1"}]
_PENNY_TICKS = [{"value": "0.01"}]
_INDEXES = {"SPX", "VIX", "VIX9D"}
_OPEN_TYPES = {"Limit", "Market", "Stop", "Stop Limit"}


def _money(value: float) -> str:
    return f"{value:.2f}"


def _fridays_after(day: date, count: int) -> list[date]:
    first = day + timedelta(days=(4 - day.weekday()) % 7 or 7)
    return [first + timedelta(weeks=i) for i in range(count)]


def _underlying(symbol: str) -> str:
    parsed = parse_occ(symbol)
    if parsed is None:
        raise BrokerError(f"The demo trades options only, not {symbol!r}.")
    return parsed[0]


def _units(raw: dict) -> int:
    return math.gcd(*(int(leg["quantity"]) for leg in raw["legs"]))


class _Account:
    """What both pretend brokers share: the account, its balance and its history."""

    name = ""
    broker = ""

    def __init__(self, market: PretendMarket, book: Book) -> None:
        self.market = market
        self.book = book

    def _check(self, account_number: str) -> None:
        if account_number != self.book.account:
            raise BrokerError(f"{self.name} has no account {account_number}.")

    def accounts(self) -> list[BrokerAccount]:
        return [
            BrokerAccount(self.broker, self.book.account, self.book.nickname,
                          self.book.account_type)
        ]  # fmt: skip

    def _last(self, ticker: str) -> float | None:
        try:
            return self.market.last(ticker)
        except KeyError:
            return None

    def balance(self, account_number: str) -> Balance:
        self._check(account_number)
        value = self.book.cash
        for symbol, quantity in self.book.options.items():
            mark = self.market.option_mark(symbol)
            value += Decimal(str(round(mark, 2))) * quantity * MULTIPLIER
        for ticker, (shares, average) in self.book.stocks.items():
            last = self._last(ticker)
            value += shares * (Decimal(str(round(last, 2))) if last is not None else average)
        buying_power = self.book.cash - max_loss(self.book.options)
        return Balance(
            net_liquidating_value=value.quantize(CENT),
            cash=self.book.cash.quantize(CENT),
            buying_power=buying_power.quantize(CENT),
        )

    def balance_history(self, account_number: str, start: date, end: date) -> list[DailyValue]:
        self._check(account_number)
        return [v for v in self.book.history if start <= v.day <= end]

    def transactions(self, account_number: str, start: date, end: date) -> list[RawTransaction]:
        self._check(account_number)
        return self.book.transactions_between(start, end)

    def stock_positions(self, account_number: str | None = None) -> list[StockPosition]:
        """Shares held. Schwab's real client takes no account (it lists them all);
        TastyTrade's takes one."""
        if account_number is not None:
            self._check(account_number)
        found = []
        for ticker, (shares, average) in sorted(self.book.stocks.items()):
            close = self._prev_close(ticker)
            found.append(
                StockPosition(
                    f"{self.broker}:{self.book.account}",
                    ticker,
                    shares,
                    average,
                    Decimal(_money(close)) if close is not None else None,
                )
            )
        return found

    def _prev_close(self, ticker: str) -> float | None:
        try:
            return self.market.prev_close(ticker)
        except KeyError:
            return None

    def _option_prev_close(self, symbol: str) -> float | None:
        """An option's price at the previous session's close."""
        parsed = parse_occ(symbol)
        spot = self._prev_close(parsed[0]) if parsed else None
        if spot is None:
            return None
        day = self.market.now.astimezone(NY).date() - timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return self.market.option_mark_at(symbol, spot, datetime.combine(day, CLOSE, tzinfo=NY))


class PretendTasty(_Account):
    """The trading broker: chains, quotes, orders and fills."""

    name = "TastyTrade"
    broker = "tastytrade"

    def __init__(self, market: PretendMarket, book: Book) -> None:
        super().__init__(market, book)
        self._chains: dict[str, list[dict]] = {}

    def quote_token(self) -> tuple[str, str]:
        """Never used to connect: the demo's quotes come from the pretend market."""
        return "demo", "demo://"

    # ----- positions -----
    def option_positions(self, account_number: str) -> dict[str, int]:
        self._check(account_number)
        return {k: v for k, v in self.book.options.items() if v}

    # ----- chains and quotes -----
    def option_chain(self, symbol: str) -> list[dict]:
        symbol = symbol.upper()
        if symbol not in _CHAINS:
            return []
        if symbol not in self._chains:
            self._chains[symbol] = [self._chain(symbol)]
        return self._chains[symbol]

    def _chain(self, symbol: str) -> dict:
        root, step, zero_dte = _CHAINS[symbol]
        underlying = "SPX" if root == "SPXW" else root
        spot = Decimal(str(self.market.prev_close(underlying)))
        low = (spot * (1 - _STRIKE_RANGE) / step).to_integral_value(ROUND_CEILING) * step
        high = (spot * (1 + _STRIKE_RANGE) / step).to_integral_value(ROUND_FLOOR) * step
        strikes = range(int(low), int(high) + 1, step)
        days = ([DEMO_DAY] if zero_dte else []) + _fridays_after(DEMO_DAY, _WEEKLIES)
        return {
            "root-symbol": root,
            "underlying-symbol": underlying,
            "expirations": [
                {
                    "expiration-date": day.isoformat(),
                    "expiration-type": "Weekly",
                    "settlement-type": "PM",
                    "strikes": [
                        {
                            "strike-price": f"{k}.0",
                            "call": occ_symbol(root, OptionDetails("CALL", Decimal(k), day)),
                            "put": occ_symbol(root, OptionDetails("PUT", Decimal(k), day)),
                        }
                        for k in strikes
                    ],
                }
                for day in days
            ],
        }

    def equity_tick_sizes(self, symbol: str) -> list[dict]:
        """SPX's options trade in 0.05 under 3.00 and 0.10 above; the ETFs' in pennies."""
        return list(_INDEX_TICKS if symbol.upper() in ("SPX", "SPXW") else _PENNY_TICKS)

    def market_data(self, by_type: dict[str, list[str]]) -> list[dict]:
        items = []
        for kind, symbols in by_type.items():
            for symbol in symbols or []:
                item = self._snapshot(kind, symbol)
                if item is not None:
                    items.append(item)
        return items

    def _snapshot(self, kind: str, symbol: str) -> dict | None:
        option = kind == "equity-option"
        try:
            bid, ask, last = self.market.quote(symbol)
        except KeyError, ValueError:
            return None
        close = self._option_prev_close(symbol) if option else self._prev_close(symbol)
        kind = "Equity Option" if option else "Index" if symbol in _INDEXES else "Equity"
        return {
            "symbol": symbol,
            "instrument-type": kind,
            "updated-at": self.market.now.isoformat(),
            "bid": _money(bid),
            "ask": _money(ask),
            "mid": _money((bid + ask) / 2),
            "mark": _money((bid + ask) / 2),
            "last": _money(last),
            "prev-close": _money(close) if close is not None else None,
        }

    def market_metrics(self, symbols: list[str]) -> list[dict]:
        """IV and liquidity per symbol. No earnings: the demo has no reports."""
        vix = self._last("VIX") or VIX_BASE
        found = []
        for symbol in symbols:
            if symbol not in VOL:
                continue
            iv = VOL[symbol] * vix / VIX_BASE
            found.append(
                {
                    "symbol": symbol,
                    "implied-volatility-index": f"{iv:.4f}",
                    "implied-volatility-index-rank": f"{min(1.0, max(0.0, vix / 40)):.4f}",
                    "implied-volatility-percentile": f"{min(1.0, max(0.0, vix / 35)):.4f}",
                    "liquidity-rating": 4,
                }
            )
        return found

    # ----- orders -----
    def preview_order(self, account_number: str, order: dict) -> dict:
        """A dry run: what the order would cost and need, never placed."""
        self._check(account_number)
        self._validate(order)
        raw = self._raw(order, "0", "Received")
        units = _units(raw)
        legs = raw["legs"]
        opening = {
            plain(leg["symbol"]): int(leg["quantity"]) * (1 if buy else -1)
            for leg, (buy, _) in zip(legs, _shape(raw), strict=True)
            if leg["action"].endswith("Open")
        }
        isolated = max_loss(opening)
        if order.get("price") is not None:
            per_unit = Decimal(str(order["price"]))
            if order.get("price-effect") == "Debit":
                per_unit = -per_unit
        else:
            per_unit = -Decimal(_money(self._cost(raw, self.market.now, {})))
        trade = per_unit * units * MULTIPLIER  # + credit, - debit
        fees: dict[str, Decimal] = {}
        for leg in legs:
            for name, amount in option_fees(leg["symbol"], int(leg["quantity"])).items():
                fees[name] = fees.get(name, Decimal(0)) + amount
        total_fees = sum(fees.values(), Decimal(0))
        change = isolated - trade + total_fees  # + uses buying power
        current = self.balance(account_number).buying_power
        new = current - change

        def effect(amount: Decimal) -> str:
            return "Debit" if amount > 0 else "Credit" if amount < 0 else "None"

        fee_fields = {}
        for name, amount in fees.items():
            fee_fields[name] = str(amount)
            fee_fields[f"{name}-effect"] = "Debit"
        return {
            "order": raw,
            "warnings": [],
            "buying-power-effect": {
                "change-in-margin-requirement": str(isolated),
                "change-in-margin-requirement-effect": "Debit",
                "change-in-buying-power": str(abs(change)),
                "change-in-buying-power-effect": effect(change),
                "current-buying-power": str(abs(current)),
                "current-buying-power-effect": "Credit" if current >= 0 else "Debit",
                "new-buying-power": str(abs(new)),
                "new-buying-power-effect": "Credit" if new >= 0 else "Debit",
                "isolated-order-margin-requirement": str(isolated),
                "isolated-order-margin-requirement-effect": "Debit",
                "is-spread": len(legs) > 1,
                "impact": str(abs(change)),
                "effect": effect(change),
            },
            "fee-calculation": {
                **fee_fields,
                "total-fees": str(total_fees),
                "total-fees-effect": "Debit",
            },
            "order-costs": {
                "estimated-trade-cost": str(abs(trade)),
                "estimated-trade-cost-effect": "Credit" if trade >= 0 else "Debit",
            },
        }

    def place_order(self, account_number: str, order: dict) -> str:
        self._check(account_number)
        self._validate(order)
        oid = self.book.new_order_id()
        self.book.orders[oid] = self._raw(order, oid, LIVE)
        return oid

    def replace_order(self, account_number: str, order_id: str, order: dict) -> str:
        self._check(account_number)
        old = self._working(order_id)
        self._validate(order)
        oid = self.book.new_order_id()
        raw = self._raw(order, oid, LIVE)
        raw["replaces-order-id"] = int(order_id)
        self.book.orders[oid] = raw
        self._end(old, "Cancelled")
        old["replacing-order-id"] = int(oid)
        return oid

    def cancel_order(self, account_number: str, order_id: str) -> None:
        self._check(account_number)
        self._end(self._working(order_id), "Cancelled")

    def get_order(self, account_number: str, order_id: str) -> BrokerOrder:
        self._check(account_number)
        return broker_order(self._found(order_id))

    def recent_orders(self, account_number: str, since=None) -> list[BrokerOrder]:
        """Today's orders and every order still working, like /orders/live."""
        self._check(account_number)
        today = market_date(self.market.now)
        return [
            broker_order(raw)
            for raw in self.book.orders.values()
            if raw["status"] == LIVE or market_date(_received(raw)) == today
        ]

    def working_orders(self, account_number: str) -> list[BrokerOrder]:
        return [o for o in self.recent_orders(account_number) if o.may_be_working]

    def _found(self, order_id: str) -> dict:
        raw = self.book.orders.get(str(order_id))
        if raw is None:
            raise BrokerError(f"TastyTrade has no order {order_id}.")
        return raw

    def _working(self, order_id: str) -> dict:
        raw = self._found(order_id)
        if raw["status"] != LIVE:
            raise BrokerError(f"Order {order_id} is {raw['status']}: it can't be changed.")
        return raw

    def _end(self, raw: dict, status: str) -> None:
        raw["status"] = status
        raw["terminal-at"] = raw["updated-at"] = self.market.now.isoformat()
        raw["cancellable"] = raw["editable"] = False
        self.book.triggered.discard(str(raw["id"]))

    def _validate(self, order: dict) -> None:
        kind = order.get("order-type")
        if kind not in _OPEN_TYPES:
            raise BrokerError(f"The demo doesn't take {kind!r} orders.")
        if kind in ("Limit", "Stop Limit") and not order.get("price"):
            raise BrokerError(f"A {kind} order needs a price.")
        if kind in ("Stop", "Stop Limit") and not order.get("stop-trigger"):
            raise BrokerError(f"A {kind} order needs a stop trigger.")
        legs = order.get("legs") or []
        if not legs:
            raise BrokerError("An order needs at least one leg.")
        for leg in legs:
            _underlying(leg.get("symbol", ""))
            quantity = int(leg.get("quantity") or 0)
            action = leg.get("action", "")
            if quantity < 1:
                raise BrokerError("Each leg needs at least 1 contract.")
            held = self.book.held(leg["symbol"])
            if action == "Buy to Close" and -held < quantity:
                raise BrokerError(f"Nothing short to buy back in {leg['symbol']}.")
            if action == "Sell to Close" and held < quantity:
                raise BrokerError(f"Nothing long to sell in {leg['symbol']}.")

    def _raw(self, order: dict, oid: str, status: str) -> dict:
        stamp = self.market.now.isoformat()
        raw = {
            "id": int(oid),
            "account-number": self.book.account,
            "status": status,
            "time-in-force": order.get("time-in-force", "Day"),
            "order-type": order["order-type"],
            "underlying-symbol": order["legs"][0]["symbol"][:6].strip(),
            "received-at": stamp,
            "updated-at": stamp,
            "cancellable": True,
            "editable": True,
            "legs": [
                {
                    "instrument-type": leg.get("instrument-type", "Equity Option"),
                    "symbol": leg["symbol"],
                    "quantity": int(leg["quantity"]),
                    "remaining-quantity": int(leg["quantity"]),
                    "action": leg["action"],
                    "fills": [],
                }
                for leg in order["legs"]
            ],
        }
        for key in ("price", "price-effect", "stop-trigger", "external-identifier"):
            if order.get(key) is not None:
                raw[key] = order[key]
        raw["size"] = _units(raw)
        return raw

    # ----- the bell -----
    def close_the_day(self, at: datetime) -> bool:
        """The close: Day orders still working expire, and so does any order on an
        option that expires today (other GTC orders keep working); options expiring
        today settle in cash at their underlying's last price, each as TastyTrade's own
        Receive Deliver record, so the sync closes them in the journal. True if
        anything changed."""
        changed = False
        today = market_date(at)
        for raw in self.book.orders.values():
            if raw["status"] != LIVE:
                continue
            if raw.get("time-in-force", "Day") == "Day" or any(
                (parsed := parse_occ(leg["symbol"])) and parsed[1].expiration <= today
                for leg in raw["legs"]
            ):
                self._end(raw, "Expired")
                changed = True
        for key in list(self.book.options):
            parsed = parse_occ(key)
            if parsed is None or parsed[1].expiration != today:
                continue
            underlying, d = parsed
            spot = Decimal(_money(self.market.last(underlying)))
            gap = spot - d.strike if d.put_call == "CALL" else d.strike - spot
            self.book.settle(key, max(gap, Decimal(0)), at)
            changed = True
        return changed

    # ----- matching -----
    def _cost(self, raw: dict, at: datetime, prices: dict[str, float]) -> float:
        """The order's price per unit at `at` (debit positive), from each leg's mark
        with the spot and VIX of that second (`prices`, else the market's now)."""
        return _net(_shape(raw), self._marks(raw, at, prices))

    def _marks(self, raw: dict, at: datetime, prices: dict[str, float]) -> list[float]:
        vix = prices.get("VIX")
        marks = []
        for leg in raw["legs"]:
            underlying = _underlying(leg["symbol"])
            spot = prices.get(underlying)
            if spot is None:
                spot = self.market.last(underlying)
            marks.append(self.market.option_mark_at(leg["symbol"], spot, at, vix))
        return marks

    def _debit(self, raw: dict, cost: float) -> bool:
        """Whether the order pays (a debit). A stop sent without a price-effect is
        read from its legs, else from which way the money goes now."""
        effect = raw.get("price-effect")
        if effect in ("Debit", "Credit"):
            return effect == "Debit"
        actions = {leg["action"].split()[0] for leg in raw["legs"]}
        if actions == {"Buy"}:
            return True
        if actions == {"Sell"}:
            return False
        return cost >= 0

    def _fill_cost(self, raw: dict, cost: float) -> Decimal | None:
        """The per-unit price (debit positive) the order fills at with the spread at
        `cost`, or None if it doesn't fill. A limit fills at its limit once the mark
        reaches it; a stop triggers once the mark reaches the trigger against the
        position and fills at that mark; a stop limit then works as a limit."""
        kind = raw["order-type"]
        debit = self._debit(raw, cost)
        sign = 1 if debit else -1
        mark = Decimal(_money(cost))
        if kind == "Market":
            return mark
        limit = sign * Decimal(str(raw["price"])) if raw.get("price") is not None else None
        if kind == "Limit":
            return limit if cost <= limit else None
        trigger = sign * Decimal(str(raw["stop-trigger"]))
        oid = str(raw["id"])
        if oid not in self.book.triggered:
            if cost < trigger:
                return None
            if kind == "Stop":
                return mark
            self.book.triggered.add(oid)  # a stop limit: the trigger tick fills at its mark
            return mark if cost <= limit else None
        return limit if cost <= limit else None

    def _can_close(self, raw: dict) -> bool:
        for leg in raw["legs"]:
            held, quantity = self.book.held(leg["symbol"]), int(leg["quantity"])
            if leg["action"] == "Buy to Close" and -held < quantity:
                return False
            if leg["action"] == "Sell to Close" and held < quantity:
                return False
        return True

    def match(self, ticks: list[Tick]) -> list[str]:
        """Fill the working orders the path crossed, second by second through `ticks`
        (everything `advance_to` returned). Returns the filled order ids."""
        working = [
            (raw, _received(raw)) for raw in self.book.orders.values() if raw["status"] == LIVE
        ]
        if not working or not ticks:
            return []
        seconds: dict[datetime, dict[str, float]] = {}
        for tick in ticks:
            seconds.setdefault(tick.at, {})[tick.ticker] = tick.price
        filled: list[str] = []
        for at, prices in seconds.items():
            for raw, received in working:
                if raw["status"] != LIVE or received >= at:
                    continue
                marks = self._marks(raw, at, prices)
                shape = _shape(raw)
                cost = _net(shape, marks)
                price = self._fill_cost(raw, cost)
                if price is None:
                    continue
                if not self._can_close(raw):
                    self._end(raw, "Rejected")
                    continue
                self.book.fill(raw, leg_prices(shape, marks, price), at)
                self.book.triggered.discard(str(raw["id"]))
                filled.append(str(raw["id"]))
        return filled


def _shape(raw: dict) -> list[tuple[bool, int]]:
    """Each leg as (buy, ratio): its contracts per unit of the order."""
    units = _units(raw)
    return [(leg["action"].startswith("Buy"), int(leg["quantity"]) // units) for leg in raw["legs"]]


def _net(shape: list[tuple[bool, int]], marks: list[float]) -> float:
    """Per unit, what the legs cost at these marks: debit positive."""
    return sum(m * r * (1 if buy else -1) for (buy, r), m in zip(shape, marks, strict=True))


def _received(raw: dict) -> datetime:
    return datetime.fromisoformat(raw["received-at"].replace("Z", "+00:00"))


class PretendSchwab(_Account):
    """History, stock holdings and chart history. No chains and no stream: without
    ``option_expirations`` and ``streamer_info`` the app goes to TastyTrade for those."""

    name = "Schwab"
    broker = "schwab"

    def option_positions(self, account_number: str) -> dict[str, int]:
        return {}

    def working_orders(self, account_number: str) -> list[BrokerOrder]:
        return []

    def recent_orders(self, account_number: str, since=None) -> list[BrokerOrder]:
        return []

    def quotes(self, symbols: list[str]) -> dict:
        """Schwab's /quotes shape for what the pretend market has ("$SPX", "AAPL",
        option symbols); anything else is left out."""
        found = {}
        for symbol in symbols:
            key = symbol.removeprefix("$")
            try:
                bid, ask, last = self.market.quote(key)
            except KeyError, ValueError:
                continue
            option = parse_occ(key) is not None
            close = self._option_prev_close(key) if option else self._prev_close(key)
            close = last if close is None else close
            found[symbol] = {
                "assetMainType": "OPTION" if option else ("INDEX" if key in _INDEXES else "EQUITY"),
                "symbol": symbol,
                "quote": {
                    "bidPrice": round(bid, 2),
                    "askPrice": round(ask, 2),
                    "lastPrice": round(last, 2),
                    "mark": round((bid + ask) / 2, 2),
                    "closePrice": round(close, 2),
                    "netChange": round(last - close, 2),
                    "tradeTime": int(self.market.now.timestamp() * 1000),
                },
                "regular": {
                    "regularMarketLastPrice": round(last, 2),
                    "regularMarketNetChange": round(last - close, 2),
                },
                "reference": {"product": key},
            }
        return found

    def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        """Daily closing prices from `start` to `end` (SchwabClient.daily_closes), from
        the pretend market's daily history. Unknown symbols have none."""
        try:
            closes = self.market.daily_closes(symbol.removeprefix("$"), start, end)
        except KeyError:
            return []
        return [DailyClose(symbol, day, Decimal(str(round(close, 2)))) for day, close in closes]

    def price_history(self, symbol: str, params: dict) -> list[dict]:
        """Candles in Schwab's shape ({datetime ms, open, high, low, close, volume})
        from the pretend market, for the minute or daily frequency asked."""
        ticker = symbol.removeprefix("$")
        daily = params.get("frequencyType") in ("daily", "weekly", "monthly")
        minutes = 1440 if daily else max(1, int(params.get("frequency") or 1))
        try:
            candles = self.market.candles(ticker, minutes, 100_000)
        except KeyError:
            return []
        start = params.get("startDate")
        end = params.get("endDate")
        return [
            {
                "datetime": c["time"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
            for c in candles
            if (start is None or c["time"] >= int(start)) and (end is None or c["time"] <= int(end))
        ]
