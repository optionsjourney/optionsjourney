"""The Trade page's working orders (#90) and the Pending drawer's (#292): the
account's working list, with Cancel and Replace for the orders this app sent for you.
Every other order (a bot's, a managed trade's, or one placed outside the app, at your
broker or in another app; #293) is shown but never changed here. Who owns an order is
read again at every Cancel or Replace. Both go through the one order path
(services/orders) as your orders, so Freeze, the checks and size limits all apply."""

import dataclasses
import logging
import math
import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.core import pending
from optionsjourney.core.market import market_date
from optionsjourney.db.models import Account, Bot, OrderLog
from optionsjourney.services import bot_adopt, orders
from optionsjourney.trading.orders import BrokerOrder, Order, OrderStatus

logger = logging.getLogger(__name__)

SAME_SIDE = "Keep the same credit or debit; Cancel and review a new order to switch."
PART_FILLED = "Part of this order filled. Cancel it and review a new order for the rest."
NOT_WORKING = "This order isn't working any more."
COULDNT_READ = "Couldn't read the order from your broker."
OUTSIDE = "Placed outside the app. Change or cancel it at your broker."
UNKNOWN_PART = (
    "The app sent this order but can't tell which part of it owns it. "
    "Change or cancel it at your broker."
)
CHANGING = "Your broker is already changing this order."
# The external identifier services/orders puts on every TastyTrade attempt (uuid4 hex[:20])
APP_TAG = re.compile(r"[0-9a-f]{20}")


@dataclasses.dataclass(frozen=True)
class Owner:
    """Who an order belongs to: "you" (sent from this app for you), "bot", "managed"
    (a managed trade's rules), "unknown" (the app sent it, but which part of it can't be
    told) or "outside" (placed at the broker or in another app). Only yours can be
    changed here (#293). `order` is your logged Order (as a dict)."""

    kind: str
    name: str | None = None
    link: str | None = None  # the bot run's page, when known
    order: dict | None = dataclasses.field(default=None, compare=False, repr=False)

    @property
    def changeable(self) -> bool:
        return self.kind == "you"

    @property
    def refusal(self) -> str | None:
        """Why the order isn't changed here, or None when it can be."""
        if self.changeable:
            return None
        if self.kind == "outside":
            return OUTSIDE
        if self.kind == "unknown":
            return UNKNOWN_PART
        name = self.name or ("a bot" if self.kind == "bot" else "this trade's rules")
        return f"Managed by {name}, use its controls."

    def shown(self) -> dict:
        return {"kind": self.kind, "name": self.name, "link": self.link}


BROKERS = {"tastytrade": "TASTYTRADE", "schwab": "SCHWAB"}


def label(account) -> str:
    """The account's name as the Trade page and the Pending drawer show it."""
    return account.nickname or f"…{account.number[-4:]}"


def trading_accounts(session_factory) -> list[Account]:
    """The TastyTrade and Schwab accounts, detached from the session."""
    with session_factory() as session:
        rows = session.scalars(select(Account).order_by(Account.broker, Account.id)).all()
        session.expunge_all()
    return [a for a in rows if a.broker in BROKERS]


def _client(clients, broker: str):
    return next((c for c in clients if getattr(c, "broker", "") == broker), None)


def _of_row(session, row: OrderLog) -> Owner | None:
    """The owner a logged attempt names: its origin. A row of yours names you only when
    it sent or replaced an order."""
    if row.origin == "bot":
        bot = session.get(Bot, row.bot_id) if row.bot_id is not None else None
        link = f"/automation/runs/{row.bot_run_id}" if row.bot_run_id is not None else None
        return Owner("bot", bot.name if bot else None, link)
    if row.origin == "managed":
        return Owner("managed")
    if row.origin == "you" and row.action in ("send", "replace"):
        return Owner("you", order=(row.order or {}).get("order"))
    return None


def _logged(session_factory, broker: str, account: str, order_id: str) -> Owner | None:
    """The owner the newest row that changed this broker order (ok) names, or None.
    A cancel that went through ends it, a refused or failed one leaves it as it was."""
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
        return _of_row(session, row) if row is not None else None


def _tagged(session_factory, broker: str, account: str, tag: str | None) -> Owner | None:
    """TastyTrade: the attempt this order's external identifier was made for (services/
    orders tags each one), ok or not: an order accepted without its id coming back."""
    if broker != "tastytrade" or not tag:
        return None
    with session_factory() as session:
        row = session.scalars(
            select(OrderLog)
            .where(
                OrderLog.broker == broker,
                OrderLog.account_number == account,
                OrderLog.tag == tag,
            )
            .order_by(OrderLog.id.desc())
            .limit(1)
        ).first()
        return _of_row(session, row) if row is not None else None


def owner(session_factory, broker: str, account: str, found: BrokerOrder) -> Owner:
    """Who owns this broker order, in this order (review fixes, rulings 5, 6 and 9):
    1. the newest ok OrderLog row with its order id (you, a bot or a managed trade);
    2. TastyTrade: the attempt its tag was made for;
    3. a bot run that adopted the order;
    4. the app's own tag with nothing above (TastyTrade: 20 lowercase hex characters;
       Schwab: the account's learned app tag): unknown;
    5. otherwise outside."""
    order_id = found.broker_order_id
    who = _logged(session_factory, broker, account, order_id) or _tagged(
        session_factory, broker, account, found.tag
    )
    if who is not None:
        return who
    name = bot_adopt.owner(session_factory, order_id)
    if name is not None:
        return Owner("bot", name)
    if broker == "tastytrade" and found.tag and APP_TAG.fullmatch(found.tag):
        return Owner("unknown")  # the app's tag with no row: it sent it, then lost track
    if (
        broker == "schwab"
        and found.tag
        and found.tag == orders._schwab_tag(session_factory, account)
    ):
        return Owner("unknown")
    return Owner("outside")


def _part_filled(found: BrokerOrder) -> bool:
    return found.status is OrderStatus.PARTIAL or any(
        leg.remaining != leg.quantity for leg in found.legs
    )


def _change_refusal(who: Owner, found: BrokerOrder) -> str | None:
    """Why the working list doesn't offer a price change, or None when it does."""
    if not who.changeable:
        return who.refusal
    if found.changing:
        return CHANGING
    if _part_filled(found):
        return PART_FILLED
    return None


def working(session_factory, clients, broker: str, account: str, now=None) -> list[dict]:
    """The account's working orders. Raises BrokerError (or similar) from the broker."""
    client = _client(clients, broker)
    if client is None:
        return []
    now = now or datetime.now(UTC)
    today = market_date(now)
    rows = []
    for item in client.working_orders(account):
        who = owner(session_factory, broker, account, item)
        price = Order.from_dict(who.order).price if who.order else item.price
        reason = _change_refusal(who, item)
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
                "yours": who.kind == "you",
                "owner": who.shown(),
                "can_change": reason is None,
                "can_cancel": who.changeable and not item.changing,
                "reason": reason,
                "category": pending.category(item, today),
                "price": float(price) if price is not None else None,
            }
        )
    return rows


def _read(clients, broker: str, account: str, order_id: str) -> BrokerOrder | orders.Result:
    """The order fresh from the broker, or the refusal to give when it can't be read."""
    client = _client(clients, broker)
    if client is None:
        return orders.Result(
            "refuse", False, f"{orders.BROKER_NAMES.get(broker, broker)} isn't connected."
        )
    try:
        return client.get_order(account, order_id)
    except Exception:  # noqa: BLE001 -- an order we can't read is never changed
        logger.exception("couldn't read order %s before changing it", order_id)
        return orders.Result("refuse", False, COULDNT_READ)


def _your_order(who: Owner, price) -> Order | orders.Result:
    """Your logged order, or SAME_SIDE when it can't be read or the price flips sides."""
    order = Order.from_dict(who.order) if who.order else None
    if order is None or order.price is None or (order.price > 0) != (price > 0):
        return orders.Result("refuse", False, SAME_SIDE)
    return order


def cancel(
    session_factory, clients, broker: str, account: str, order_id: str, now=None
) -> orders.Result:
    """Cancel an order the app sent for you. The order is read fresh from the broker
    first (and never cancelled when it can't be), and its owner read now, not when the
    list was shown: a bot may have taken the order since."""
    found = _read(clients, broker, account, order_id)
    if isinstance(found, orders.Result):
        return found
    if not found.working:
        return orders.Result("refuse", False, NOT_WORKING)
    if found.changing:
        return orders.Result("refuse", False, CHANGING)
    who = owner(session_factory, broker, account, found)
    if not who.changeable:
        return orders.Result("refuse", False, who.refusal)
    return orders.cancel(session_factory, clients, broker, account, order_id, "you", now=now)


def replace(
    session_factory, clients, broker: str, account: str, order_id: str, price, now=None
) -> orders.Result:
    """Change the limit price of an order you placed. The legs, quantity and
    opening/closing stay as they are, so a partly filled order is never replaced
    (it would go back out at the full size; bot_chase does the same): it's read
    fresh from the broker first. Your order is rebuilt from the log, never from the
    broker's copy; any other order is refused (#293). orders.replace puts the price
    on tick."""
    who = _logged(session_factory, broker, account, order_id)
    if who is not None and not who.changeable:
        return orders.Result("refuse", False, who.refusal)
    if type(price) not in (int, float) or price == 0 or not math.isfinite(price):
        return orders.Result("refuse", False, "Set a limit price above 0.")
    order = _your_order(who, price) if who is not None else None
    if isinstance(order, orders.Result):
        return order
    found = _read(clients, broker, account, order_id)
    if isinstance(found, orders.Result):
        return found
    if who is None:
        who = owner(session_factory, broker, account, found)
        if not who.changeable:
            return orders.Result("refuse", False, who.refusal)
        order = _your_order(who, price)
        if isinstance(order, orders.Result):
            return order
    if not found.working:
        return orders.Result("refuse", False, NOT_WORKING)
    if found.changing:
        return orders.Result("refuse", False, CHANGING)
    if _part_filled(found):
        return orders.Result("refuse", False, PART_FILLED)
    new_order = dataclasses.replace(order, price=Decimal(str(price)))
    request = orders.Request(broker, account, "you", new_order)
    return orders.replace(session_factory, clients, request, order_id, now=now)
