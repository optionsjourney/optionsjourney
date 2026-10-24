"""A broker-neutral order, and the broker's orders read back (spec: the order
path). Bots, managed trades and buttons all build an Order; trading/tastytrade.py
and trading/schwab.py turn it into each broker's format. Pure.

Prices are signed: positive is a credit (we take money in), negative a debit."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from optionsjourney.core.symbols import OCC


class OrderError(ValueError):
    """An order that can't be built. The message is shown to the user."""


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    GTC_OVERNIGHT = "gtc_overnight"  # Cboe overnight hours; TastyTrade index options only


class OrderStatus(StrEnum):
    WORKING = "working"
    PARTIAL = "partial"  # partly filled, the rest still working
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


WITH_PRICE = frozenset({OrderType.LIMIT, OrderType.STOP_LIMIT})
WITH_TRIGGER = frozenset({OrderType.STOP, OrderType.STOP_LIMIT})
_LABEL = {
    OrderType.LIMIT: "limit",
    OrderType.MARKET: "market",
    OrderType.STOP: "stop",
    OrderType.STOP_LIMIT: "stop-limit",
}


def is_futures(symbol: str) -> bool:
    return symbol.lstrip().startswith("./")


def plain(symbol: str) -> str:
    """The symbol without spaces, so padded and unpadded spellings compare equal."""
    return "".join(symbol.split())


def order_effect(legs, held) -> str:
    """ "opening", "closing" or "roll" for (symbol, side) legs against the
    broker's open contracts (plain symbol -> signed). A leg closes when it
    trades against what's held; how many is the closing checks' job."""
    closes = []
    for symbol, side in legs:
        have = held.get(plain(symbol), 0)
        closes.append(
            (have > 0 and Side(side) is Side.SELL) or (have < 0 and Side(side) is Side.BUY)
        )
    if all(closes):
        return "closing"
    return "opening" if not any(closes) else "roll"


def _enum(kind, value, what):
    try:
        return kind(value)
    except ValueError:
        raise OrderError(f"Unknown {what}: {value!r}.") from None


def _decimal(value, what):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        number = None
    if number is None or not number.is_finite():
        raise OrderError(f"{value!r} isn't a valid {what}.")
    return number


def _whole(value, what) -> int:
    """A count must be a real int: not a bool, a float or a string."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise OrderError(f"The {what} must be a whole number, not {value!r}.")
    return value


def _option_symbol(symbol) -> str:
    """The canonical spelling: an OCC symbol padded to 21 characters, or a
    TastyTrade futures option ("./ESZ6 EW2Z6 261218P7000")."""
    text = symbol.strip() if isinstance(symbol, str) else ""
    if text.startswith("./"):
        if len(text.split()) == 3:
            return text
    elif match := OCC.match(text):
        root, yymmdd, put_call, strike = match.groups()
        return f"{root:<6}{yymmdd}{put_call}{strike}"
    raise OrderError(f"{symbol!r} isn't an option symbol this app can trade.")


@dataclass(frozen=True)
class Leg:
    symbol: str  # OCC "SPXW  261016P05800000", or TastyTrade's "./ESZ6 EW2Z6 261218P7000"
    side: Side
    ratio: int = 1  # contracts per unit (a butterfly's body is 2)

    def __post_init__(self):
        try:
            object.__setattr__(self, "side", Side(self.side))
        except ValueError:
            raise OrderError(f"A leg must be buy or sell, not {self.side!r}.") from None
        object.__setattr__(self, "symbol", _option_symbol(self.symbol))
        if _whole(self.ratio, "contracts per unit") < 1:
            raise OrderError("A leg needs at least 1 contract per unit.")

    @property
    def futures(self) -> bool:
        return is_futures(self.symbol)

    @property
    def root(self) -> str:
        """'SPXW' for an OCC symbol; the futures contract ('/ESZ6') for a futures option."""
        if self.futures:
            return self.symbol.split()[0][1:]
        return self.symbol[:6].strip()


@dataclass(frozen=True)
class Order:
    legs: tuple[Leg, ...]
    quantity: int  # units: spreads, or contracts of a single option
    opening: bool  # all legs open, or all close
    order_type: OrderType = OrderType.LIMIT
    price: Decimal | None = None  # signed, per unit; None for market and stop orders
    trigger: Decimal | None = None  # a stop's trigger, positive
    time_in_force: TimeInForce = TimeInForce.DAY

    def __post_init__(self):
        object.__setattr__(self, "legs", tuple(self.legs))
        object.__setattr__(self, "order_type", _enum(OrderType, self.order_type, "order type"))
        object.__setattr__(
            self, "time_in_force", _enum(TimeInForce, self.time_in_force, "time in force")
        )
        object.__setattr__(self, "price", _decimal(self.price, "price"))
        object.__setattr__(self, "trigger", _decimal(self.trigger, "trigger price"))
        label = _LABEL[self.order_type]
        if not self.legs:
            raise OrderError("An order needs at least one leg.")
        if _whole(self.quantity, "number of units") < 1:
            raise OrderError("An order needs at least 1 unit.")
        symbols = [plain(leg.symbol) for leg in self.legs]
        if len(set(symbols)) != len(symbols):
            raise OrderError("A symbol appears twice in one order.")
        if len({leg.futures for leg in self.legs}) > 1:
            raise OrderError("Futures options and stock options can't be in one order.")
        if self.order_type in WITH_PRICE:
            if self.price is None:
                raise OrderError(f"A {label} order needs a price.")
            if self.price == 0:
                raise OrderError("The price can't be zero.")
            # Schwab sends a single leg's price unsigned, so a mis-signed one would go out wrong.
            if len(self.legs) == 1 and (self.price > 0) != (self.legs[0].side is Side.SELL):
                raise OrderError(
                    "A single-leg sell takes in a credit (a positive price) and a buy pays a "
                    "debit (a negative price)."
                )
        elif self.price is not None:
            raise OrderError(f"A {label} order has no limit price.")
        if self.order_type in WITH_TRIGGER:
            if self.trigger is None or self.trigger <= 0:
                raise OrderError("A stop order needs a trigger price above zero.")
            if len(self.legs) > 1:
                raise OrderError("Stop orders can have only one leg.")
        elif self.trigger is not None:
            raise OrderError("Only stop orders have a trigger price.")
        if (
            self.time_in_force is TimeInForce.GTC_OVERNIGHT
            and self.order_type is not OrderType.LIMIT
        ):
            raise OrderError("Overnight orders must be limit orders.")

    @property
    def contracts(self) -> dict[str, int]:
        """Contracts per leg symbol, for the whole order."""
        return {leg.symbol: leg.ratio * self.quantity for leg in self.legs}

    @property
    def largest_leg(self) -> int:
        """Contracts in the order's largest leg: units times the biggest ratio. What the
        size limits count (Tracker2's scale_legs)."""
        return self.quantity * max(leg.ratio for leg in self.legs)

    def to_dict(self) -> dict:
        return {
            "legs": [
                {"symbol": leg.symbol, "side": str(leg.side), "ratio": leg.ratio}
                for leg in self.legs
            ],
            "quantity": self.quantity,
            "opening": self.opening,
            "order_type": str(self.order_type),
            "price": None if self.price is None else str(self.price),
            "trigger": None if self.trigger is None else str(self.trigger),
            "time_in_force": str(self.time_in_force),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Order:
        return cls(
            legs=tuple(
                Leg(item["symbol"], item["side"], item.get("ratio", 1)) for item in data["legs"]
            ),
            quantity=data["quantity"],
            opening=data["opening"],
            order_type=data.get("order_type", "limit"),
            price=data.get("price"),
            trigger=data.get("trigger"),
            time_in_force=data.get("time_in_force", "day"),
        )


@dataclass(frozen=True)
class BrokerLeg:
    symbol: str
    side: Side
    opening: bool
    quantity: int  # contracts
    remaining: int  # contracts not filled yet
    fill_price: Decimal | None = None  # average per contract, once something filled


@dataclass(frozen=True)
class BrokerOrder:
    """An order as the broker reports it."""

    broker_order_id: str
    status: OrderStatus
    legs: tuple[BrokerLeg, ...]
    tag: str | None = None
    entered_at: datetime | None = None
    # Bots slice 21: the order that replaced this one at the broker (TastyTrade's
    # replacing-order-id), if any
    replaced_by: str | None = None
    # The broker's limit price, shown for an order the app has no log row for.
    # Signed like Order.price (+ is a credit); None when the sign can't be told.
    price: Decimal | None = None
    changing: bool = False  # a cancel or replace is already under way at the broker

    @property
    def working(self) -> bool:
        return self.status in (OrderStatus.WORKING, OrderStatus.PARTIAL)

    @property
    def may_be_working(self) -> bool:
        """Working, or in a status the app doesn't know: the working-orders
        lists count those too, so the closing check fails closed."""
        return self.working or self.status is OrderStatus.UNKNOWN


def same_legs(order: Order, found: BrokerOrder) -> bool:
    """Whether the broker's order has exactly this order's legs and sizes."""
    mine = {
        (plain(leg.symbol), leg.side, order.opening, leg.ratio * order.quantity)
        for leg in order.legs
    }
    theirs = {(plain(item.symbol), item.side, item.opening, item.quantity) for item in found.legs}
    return mine == theirs


def filled_units(found: BrokerOrder, ratios: dict[str, int]) -> tuple[int, bool]:
    """Whole units of a combo an order filled (Tracker2's filled_units): each leg's
    filled contracts over its ratio, the smallest across the legs. Also whether any
    leg filled more than those whole units need (the legs filled unevenly).
    `ratios` is by plain symbol; a leg not in it has ratio 1."""
    done = [(leg.quantity - leg.remaining, ratios.get(plain(leg.symbol), 1)) for leg in found.legs]
    units = min(filled // ratio for filled, ratio in done)
    return units, any(filled != units * ratio for filled, ratio in done)
