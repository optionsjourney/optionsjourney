"""Pending orders across every account (#292): what the Journal's Pending drawer
shows. One snapshot reads each account's working orders in parallel, so a slow or
failing broker marks only its own account; `order_marks` gives each order's live
mid, signed so a credit is positive and comparable to the order's own price."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime
from math import gcd

from optionsjourney.services import streaming, trade_orders

logger = logging.getLogger(__name__)

_listed: dict[str, list[dict]] = {}  # order_id -> legs, as of the last snapshot
_listed_lock = threading.Lock()
COULDNT_READ = "Couldn't read this account's orders from your broker."


def snapshot(session_factory, clients, accounts, timeout_s: float = 10.0) -> dict:
    """Every account's working orders. An account that times out or fails has
    `error` set and no orders; the others are unaffected."""
    pool = ThreadPoolExecutor(max_workers=max(1, len(accounts)))
    try:
        futures = [
            pool.submit(trade_orders.working, session_factory, clients, a.broker, a.number)
            for a in accounts
        ]
        wait(futures, timeout=timeout_s)
        entries = [_entry(a, f) for a, f in zip(accounts, futures, strict=True)]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # a stuck read must not hold the page
    _remember(entries)
    return {
        "accounts": entries,
        "count": sum(len(e["orders"]) for e in entries),
        "errors": sum(e["error"] is not None for e in entries),
        "as_of": datetime.now(UTC).isoformat(),
    }


def _remember(entries) -> None:
    """Keep the legs of every listed order, replacing the last listing, so the live
    mids can be priced without asking a broker again."""
    legs = {o["order_id"]: o["legs"] for e in entries for o in e["orders"]}
    with _listed_lock:
        _listed.clear()
        _listed.update(legs)


def listed_marks(hub, ids) -> dict[str, float | None]:
    """Live mids for the orders in the last snapshot; other ids are left out. Never
    calls a broker."""
    with _listed_lock:
        orders = [{"order_id": i, "legs": _listed[i]} for i in ids if i in _listed]
    return order_marks(hub, orders)


def _entry(account, future) -> dict:
    orders, error = [], None
    if not future.done():
        future.cancel()
        error = COULDNT_READ
    else:
        try:
            orders = future.result()
        except Exception:  # noqa: BLE001 -- one account's failure never blanks the others
            logger.exception("couldn't read %s working orders", account.broker)
            error = COULDNT_READ
    return {
        "account_id": account.id,
        "broker": trade_orders.BROKERS[account.broker],
        "label": trade_orders.label(account),
        "orders": orders,
        "error": error,
    }


def order_marks(hub, orders) -> dict[str, float | None]:
    """Each order's live mid per unit: sell legs count +, buy legs -, each by its
    quantity over the legs' common divisor. None when any leg has no mark. Subscribes
    every leg on the hub so the marks fill in on the next call."""
    keys = {leg["symbol"] for order in orders for leg in order["legs"]}
    hub.subscribe(keys)
    found = streaming.marks(hub, keys)
    return {order["order_id"]: _mid(order["legs"], found) for order in orders}


def _mid(legs, found) -> float | None:
    unit = 0
    for leg in legs:
        unit = gcd(unit, int(leg["quantity"]))
    total = 0
    for leg in legs:
        mark = found.get(leg["symbol"])
        if mark is None or unit == 0:
            return None
        sign = 1 if leg["side"] == "sell" else -1
        total += sign * mark * (int(leg["quantity"]) // unit)
    return round(float(total), 2)
