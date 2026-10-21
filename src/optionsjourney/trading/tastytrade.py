"""Neutral orders to TastyTrade's order format, and TastyTrade's orders read
back (spec: the order path). Pure. The body is what /accounts/{n}/orders, its
dry run and its replace (PUT) take."""

from datetime import datetime
from decimal import Decimal

from optionsjourney.trading.orders import (
    BrokerLeg,
    BrokerOrder,
    Order,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)

_ACTION = {
    (Side.BUY, True): "Buy to Open",
    (Side.SELL, True): "Sell to Open",
    (Side.BUY, False): "Buy to Close",
    (Side.SELL, False): "Sell to Close",
}
_TYPE = {
    OrderType.LIMIT: "Limit",
    OrderType.MARKET: "Market",
    OrderType.STOP: "Stop",
    OrderType.STOP_LIMIT: "Stop Limit",
}
# "GTC Ext Overnight": Cboe Global Trading Hours, verified live in Tracker2 (2026-10-02)
_TIF = {
    TimeInForce.DAY: "Day",
    TimeInForce.GTC: "GTC",
    TimeInForce.GTC_OVERNIGHT: "GTC Ext Overnight",
}
_WORKING = {
    "Received", "Routed", "In Flight", "Live", "Contingent", "Cancel Requested",
    "Replace Requested",
}  # fmt: skip
_STATUS = {
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELLED,
    "Removed": OrderStatus.CANCELLED,
    "Partially Removed": OrderStatus.CANCELLED,
    "Expired": OrderStatus.EXPIRED,
    "Rejected": OrderStatus.REJECTED,
}


def _number(value: Decimal) -> str:
    return format(abs(value), "f")


def order_body(order: Order, tag: str | None = None) -> dict:
    """The JSON body for this order. `tag` goes in external-identifier, which
    TastyTrade echoes back: how the app finds its own orders later."""
    body = {
        "time-in-force": _TIF[order.time_in_force],
        "order-type": _TYPE[order.order_type],
    }
    if order.price is not None:
        body["price"] = _number(order.price)
        body["price-effect"] = "Credit" if order.price > 0 else "Debit"
    if order.trigger is not None:
        body["stop-trigger"] = _number(order.trigger)
    if tag:
        body["external-identifier"] = tag
    body["legs"] = [
        {
            "instrument-type": "Future Option" if leg.futures else "Equity Option",
            "symbol": leg.symbol,
            "quantity": leg.ratio * order.quantity,
            "action": _ACTION[(leg.side, order.opening)],
        }
        for leg in order.legs
    ]
    return body


def _count(value) -> int:
    return int(Decimal(str(value or 0)))


def _time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def _average_fill(fills: list) -> Decimal | None:
    """The quantity-weighted average of a leg's priced fills, or None if none.
    A fill with no price is skipped: counting it as $0 would skew the average."""
    total = count = Decimal(0)
    for fill in fills or []:
        price = fill.get("fill-price")
        if price is None or str(price).strip() == "":
            continue
        quantity = Decimal(str(fill.get("quantity") or 0))
        total += Decimal(str(price)) * quantity
        count += quantity
    return (total / count).quantize(Decimal("0.0001")).normalize() if count else None


def broker_order(raw: dict) -> BrokerOrder:
    legs = []
    for leg in raw.get("legs", []):
        action = leg.get("action", "")
        quantity = _count(leg.get("quantity"))
        legs.append(
            BrokerLeg(
                symbol=leg.get("symbol", ""),
                side=Side.BUY if action.startswith("Buy") else Side.SELL,
                opening=action.endswith("Open"),
                quantity=quantity,
                remaining=_count(leg.get("remaining-quantity", quantity)),
                fill_price=_average_fill(leg.get("fills")),
            )
        )
    text = raw.get("status", "")
    if text in _WORKING:
        partial = any(leg.remaining < leg.quantity for leg in legs)
        status = OrderStatus.PARTIAL if partial else OrderStatus.WORKING
    else:
        status = _STATUS.get(text, OrderStatus.UNKNOWN)
    return BrokerOrder(
        broker_order_id=str(raw.get("id", "")),
        status=status,
        legs=tuple(legs),
        tag=raw.get("external-identifier") or None,
        entered_at=_time(raw.get("received-at")),
        replaced_by=str(raw["replacing-order-id"]) if raw.get("replacing-order-id") else None,
    )
