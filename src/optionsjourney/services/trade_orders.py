"""The Trade page's working orders (#90): the account's working list, with Cancel
and Replace for the orders this app sent for you. Anything else (a bot's, a
managed trade's, one placed at the broker) is shown but never changed here.
Cancel and Replace go through the one order path (services/orders)."""

import dataclasses
import logging
import math
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.db.models import OrderLog
from optionsjourney.services import orders
from optionsjourney.trading.orders import Order

logger = logging.getLogger(__name__)

NOT_YOURS = "Only orders you placed from the app can be changed here."
SAME_SIDE = "Keep the same credit or debit; Cancel and review a new order to switch."
PART_FILLED = "Part of this order filled. Cancel it and review a new order for the rest."
NOT_WORKING = "This order isn't working any more."
COULDNT_READ = "Couldn't read the order from your broker."


def _client(clients, broker: str):
    return next((c for c in clients if getattr(c, "broker", "") == broker), None)


def _yours(session_factory, broker: str, account: str, order_id: str) -> dict | None:
    """The logged order the app sent for you under this broker order id, or None.
    Only the newest row that changed the order counts (ok): a cancel that
    went through ends it, a refused or failed one leaves it yours."""
    with session_factory() as session:
        row = session.scalars(
            select(OrderLog)
            .where(
                OrderLog.broker == broker,
                OrderLog.account_number == account,
                OrderLog.broker_order_id == order_id,
                OrderLog.ok.is_(True),
            )
            .order_by(OrderLog.id.desc())
            .limit(1)
        ).first()
        if row and row.origin == "you" and row.action in ("send", "replace") and row.ok:
            return row.order["order"]
    return None


def working(session_factory, clients, broker: str, account: str) -> list[dict]:
    """The account's working orders. Raises BrokerError (or similar) from the broker."""
    client = _client(clients, broker)
    if client is None:
        return []
    rows = []
    for item in client.working_orders(account):
        logged = _yours(session_factory, broker, account, item.broker_order_id)
        price = Order.from_dict(logged).price if logged else None
        rows.append(
            {
                "order_id": item.broker_order_id,
                "status": str(item.status),
                "legs": [
                    {
                        "symbol": leg.symbol,
                        "side": str(leg.side),
                        "opening": leg.opening,
                        "quantity": leg.quantity,
                        "remaining": leg.remaining,
                    }
                    for leg in item.legs
                ],
                "entered_at": item.entered_at.isoformat() if item.entered_at else None,
                "yours": logged is not None,
                "price": float(price) if price is not None else None,
            }
        )
    return rows


def cancel(session_factory, clients, broker: str, account: str, order_id: str) -> orders.Result:
    if _yours(session_factory, broker, account, order_id) is None:
        return orders.Result("refuse", False, NOT_YOURS)
    return orders.cancel(session_factory, clients, broker, account, order_id, "you")


def replace(
    session_factory, clients, broker: str, account: str, order_id: str, price
) -> orders.Result:
    """Change the limit price of an order you placed. The legs, quantity and
    opening/closing stay as sent, so a partly filled order is never replaced
    (it would go back out at the full size; bot_chase and Tracker2 do the same):
    it's read fresh from the broker first. orders.replace puts the price on tick."""
    logged = _yours(session_factory, broker, account, order_id)
    if logged is None:
        return orders.Result("refuse", False, NOT_YOURS)
    if type(price) not in (int, float) or price == 0 or not math.isfinite(price):
        return orders.Result("refuse", False, "Set a limit price above 0.")
    order = Order.from_dict(logged)
    if order.price is None or (order.price > 0) != (price > 0):
        return orders.Result("refuse", False, SAME_SIDE)
    client = _client(clients, broker)
    if client is None:
        return orders.Result(
            "refuse", False, f"{orders.BROKER_NAMES.get(broker, broker)} isn't connected."
        )
    try:
        found = client.get_order(account, order_id)
    except Exception:  # noqa: BLE001 -- an order we can't read is never replaced
        logger.exception("couldn't read order %s before replacing it", order_id)
        return orders.Result("refuse", False, COULDNT_READ)
    if not found.working:
        return orders.Result("refuse", False, NOT_WORKING)
    if any(leg.remaining < leg.quantity for leg in found.legs):
        return orders.Result("refuse", False, PART_FILLED)
    new_order = dataclasses.replace(order, price=Decimal(str(price)))
    request = orders.Request(broker, account, "you", new_order)
    return orders.replace(session_factory, clients, request, order_id)
