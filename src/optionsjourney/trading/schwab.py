"""Neutral orders to Schwab's order format, and Schwab's orders read back
(spec: the order path). Pure.

From Tracker2's services/bots/schwab_orders.py, checked there against the Trader
API spec and 349 real orders: multi-leg orders are NET_CREDIT / NET_DEBIT with an
unsigned price, and VERTICAL/CALENDAR/DIAGONAL/IRON_CONDOR/BUTTERFLY/CUSTOM are
all accepted for SPXW. Schwab takes no futures orders and has no overnight
time in force."""

from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from optionsjourney.trading.orders import (
    WITH_TRIGGER,
    BrokerLeg,
    BrokerOrder,
    Order,
    OrderError,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)

_INSTRUCTION = {
    (Side.BUY, True): "BUY_TO_OPEN",
    (Side.SELL, True): "SELL_TO_OPEN",
    (Side.BUY, False): "BUY_TO_CLOSE",
    (Side.SELL, False): "SELL_TO_CLOSE",
}
_DURATION = {TimeInForce.DAY: "DAY", TimeInForce.GTC: "GOOD_TILL_CANCEL"}
_SINGLE_TYPE = {
    OrderType.LIMIT: "LIMIT",
    OrderType.MARKET: "MARKET",
    OrderType.STOP: "STOP",
    OrderType.STOP_LIMIT: "STOP_LIMIT",
}
_WORKING = {
    "AWAITING_PARENT_ORDER", "AWAITING_CONDITION", "AWAITING_STOP_CONDITION",
    "AWAITING_MANUAL_REVIEW", "ACCEPTED", "AWAITING_UR_OUT", "PENDING_ACTIVATION", "QUEUED",
    "WORKING", "NEW", "AWAITING_RELEASE_TIME", "PENDING_ACKNOWLEDGEMENT", "PENDING_RECALL",
    "PENDING_CANCEL", "PENDING_REPLACE",
}  # fmt: skip
_STATUS = {
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "REPLACED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}


def _parts(symbol: str) -> tuple[str, str, str, int]:
    """(root, yymmdd, C/P, strike in thousandths) from a padded OCC symbol."""
    return symbol[:6].strip(), symbol[6:12], symbol[12], int(symbol[13:21])


def strategy_type(order: Order) -> str:
    legs = order.legs
    if len(legs) == 1:
        return "NONE"
    parts = [_parts(leg.symbol) for leg in legs]
    expirations = {p[1] for p in parts}
    rights = {p[2] for p in parts}
    strikes = {p[3] for p in parts}
    ratios = [leg.ratio for leg in legs]
    buys = [leg.side is Side.BUY for leg in legs]
    if len(legs) == 2 and len(rights) == 1 and ratios[0] == ratios[1] and buys[0] != buys[1]:
        if len(expirations) == 1:
            return "VERTICAL"
        return "CALENDAR" if len(strikes) == 1 else "DIAGONAL"
    if (
        len(legs) == 4
        and len(expirations) == 1
        and rights == {"C", "P"}
        and len(set(ratios)) == 1
        and sum(buys) == 2
    ):
        by_right = {
            r: sorted((p[3], b) for p, b in zip(parts, buys, strict=True) if p[2] == r)
            for r in rights
        }
        puts, calls = by_right["P"], by_right["C"]
        if (
            len(puts) == 2
            and len(calls) == 2
            and puts[0][1]
            and not puts[1][1]
            and calls[1][1]
            and not calls[0][1]
        ):
            return "IRON_CONDOR"
    if len(legs) == 3 and len(expirations) == 1 and len(rights) == 1:
        lo, mid, hi = sorted(zip(parts, buys, ratios, strict=True), key=lambda t: t[0][3])
        if (
            lo[1] == hi[1] != mid[1]
            and mid[2] == lo[2] + hi[2]
            and mid[0][3] - lo[0][3] == hi[0][3] - mid[0][3]
        ):
            return "BUTTERFLY"
    return "CUSTOM"


def _money(value: Decimal) -> str:
    return f"{abs(value):.2f}"


def order_body(order: Order) -> dict:
    """The JSON body for POST/PUT /accounts/{hash}/orders and previewOrder."""
    if order.legs[0].futures:
        raise OrderError("Schwab can't take futures orders.")
    if order.time_in_force is TimeInForce.GTC_OVERNIGHT:
        raise OrderError("Schwab has no overnight orders.")
    body = {
        "session": "NORMAL",
        "duration": _DURATION[order.time_in_force],
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": strategy_type(order),
    }
    if len(order.legs) == 1 or order.order_type in WITH_TRIGGER:
        body["orderType"] = _SINGLE_TYPE[order.order_type]
        if order.price is not None:
            body["price"] = _money(order.price)
    elif order.order_type is OrderType.MARKET:
        body["orderType"] = "MARKET"
    else:
        body["orderType"] = "NET_CREDIT" if order.price > 0 else "NET_DEBIT"
        body["price"] = _money(order.price)
    if order.trigger is not None:
        body["stopPrice"] = _money(order.trigger)
    body["orderLegCollection"] = [
        {
            "instruction": _INSTRUCTION[(leg.side, order.opening)],
            "quantity": leg.ratio * order.quantity,
            "instrument": {"symbol": leg.symbol, "assetType": "OPTION"},
        }
        for leg in order.legs
    ]
    return body


def _time(text: str | None) -> datetime | None:
    """Schwab's "2026-10-13T14:00:00+0000" (fractions of a second allowed);
    None if it can't be read, which the unclear-order matching treats as no match."""
    for pattern in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(text, pattern)
        except TypeError, ValueError:
            continue
    return None


def _average_fill(executions: list[tuple[Decimal | None, Decimal]]) -> Decimal | None:
    """The quantity-weighted average of a leg's priced executions, or None if none.
    An execution with no price is skipped: counting it as $0 would skew the average."""
    executions = [(price, quantity) for price, quantity in executions if price is not None]
    count = sum((quantity for _, quantity in executions), Decimal(0))
    if not count:
        return None
    total = sum((price * quantity for price, quantity in executions), Decimal(0))
    return (total / count).quantize(Decimal("0.0001")).normalize()


def _signed_price(raw: dict, legs: tuple[BrokerLeg, ...]) -> Decimal | None:
    """The signed price from Schwab's orderType and unsigned price. NET_CREDIT /
    NET_DEBIT carry the sign in the name, and NET_ZERO is 0; a single-leg LIMIT or
    STOP_LIMIT takes its sign from the leg (a sell is +). A multi-leg one has no
    price: its sign can't be told."""
    kind = raw.get("orderType")
    price = None if raw.get("price") is None else Decimal(str(raw["price"]))
    if kind == "NET_ZERO":
        return Decimal(0)
    if kind in ("NET_CREDIT", "NET_DEBIT"):
        return -price if price is not None and kind == "NET_DEBIT" else price
    if len(legs) > 1:
        return None
    if price is not None and legs and legs[0].side is Side.BUY:
        return -price
    return price


_CHANGING = {"PENDING_CANCEL", "PENDING_REPLACE"}


def broker_order(raw: dict) -> BrokerOrder:
    units = Decimal(str(raw.get("quantity") or 0))
    filled = Decimal(str(raw.get("filledQuantity") or 0))
    share = filled / units if units else Decimal(0)
    executed: dict[int, list[tuple[Decimal | None, Decimal]]] = {}
    for activity in raw.get("orderActivityCollection") or []:
        for execution in activity.get("executionLegs") or []:
            price = execution.get("price")
            executed.setdefault(execution.get("legId"), []).append(
                (
                    None if price is None else Decimal(str(price)),
                    Decimal(str(execution.get("quantity") or 0)),
                )
            )
    legs = []
    for leg in raw.get("orderLegCollection", []):
        instruction = leg.get("instruction", "")
        quantity = int(Decimal(str(leg.get("quantity") or 0)))
        done = int((quantity * share).to_integral_value(rounding=ROUND_FLOOR))
        legs.append(
            BrokerLeg(
                symbol=(leg.get("instrument") or {}).get("symbol", ""),
                side=Side.BUY if instruction.startswith("BUY") else Side.SELL,
                opening=instruction.endswith("OPEN"),
                quantity=quantity,
                remaining=quantity - done,
                fill_price=_average_fill(executed.get(leg.get("legId"), [])),
            )
        )
    text = raw.get("status", "")
    if text in _WORKING:
        status = OrderStatus.PARTIAL if filled > 0 else OrderStatus.WORKING
    else:
        status = _STATUS.get(text, OrderStatus.UNKNOWN)
    return BrokerOrder(
        broker_order_id=str(raw.get("orderId", "")),
        status=status,
        legs=tuple(legs),
        tag=raw.get("tag") or None,
        entered_at=_time(raw.get("enteredTime")),
        price=_signed_price(raw, tuple(legs)),
        changing=text in _CHANGING,
    )
