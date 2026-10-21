"""The Trade page's ticket (#90): its legs as a broker-neutral order, checked
and previewed through the one order path. Review only previews, and keeps the
order it checked under a one-time code. Confirm sends that stored order, once,
through the same path; nothing else here sends."""

import logging
import math
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from optionsjourney.bots import futures as futures_rules
from optionsjourney.bots import underlyings
from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.chain import occ_from_dxlink
from optionsjourney.core.symbols import parse_occ
from optionsjourney.services import orders
from optionsjourney.trading import ticks
from optionsjourney.trading.orders import Leg, Order, OrderError, Side, TimeInForce, order_effect

logger = logging.getLogger(__name__)

TIFS = {"day": TimeInForce.DAY, "gtc": TimeInForce.GTC, "gtc_overnight": TimeInForce.GTC_OVERNIGHT}
TIF_WORDS = {
    TimeInForce.DAY: "Day",
    TimeInForce.GTC: "GTC",
    TimeInForce.GTC_OVERNIGHT: "GTC overnight",
}
REVIEW_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class Reviewed:
    result: orders.Result
    code: str | None  # the one-time code Confirm needs; None unless the preview passed
    summary: str  # the order in plain words; "" when there is none
    futures: bool = False  # bots slice 17: a futures order, which only a bot may send


_PENDING: dict[str, tuple] = {}  # code -> (broker, account, order, opening, reviewed at)
_LOCK = threading.Lock()


def _keep(broker: str, account: str, order: Order, now: datetime) -> str:
    code = secrets.token_urlsafe(16)
    with _LOCK:
        for key in [k for k, p in _PENDING.items() if now - p[4] > REVIEW_TTL]:
            del _PENDING[key]
        _PENDING[code] = (broker, account, order, order.opening, now)
    return code


def _take(code, broker: str, account: str, now: datetime) -> Order | None:
    """The reviewed order for this account, used up; None if unknown,
    used, expired or another account's."""
    with _LOCK:
        pending = _PENDING.get(code) if isinstance(code, str) else None
        if pending is None or pending[0] != broker or pending[1] != account:
            return None
        del _PENDING[code]
    return None if now - pending[4] > REVIEW_TTL else pending[2]


def _strike(strike: Decimal) -> str:
    text = format(strike, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _summary(order: Order, multiplier=None) -> str:
    parts = []
    for leg in order.legs:
        parsed = parse_occ(leg.symbol)
        option = parsed[1] if parsed else futures_rules.option_of(leg.symbol)
        if option is None:
            parts.append(f"{leg.side.value.capitalize()} {leg.ratio} {leg.symbol}")
            continue
        parts.append(
            f"{leg.side.value.capitalize()} {leg.ratio} {leg.root} "
            f"{option.expiration.day} {option.expiration:%b} "
            f"{_strike(option.strike)}{option.put_call[0]}"
        )
    legs = " / ".join(parts)
    if multiplier is not None:  # bots slice 17: a futures option's dollars a point
        legs = f"{legs} (×{multiplier:f})"
    if order.quantity > 1:
        legs = f"{order.quantity} × ({legs})"
    price = order.price or Decimal(0)
    kind = "credit" if price > 0 else "debit"
    return f"{legs} · ${abs(price):.2f} {kind} · {TIF_WORDS[order.time_in_force]}"


def _refuse(message: str) -> orders.Result:
    return orders.Result("refuse", False, message)


def _whole(value) -> bool:
    return type(value) is int


FUTURES_LATER = "Futures orders from the ticket come later."
FUTURES_TO_A_BOT = (
    "A futures ticket can only be sent as a bot's entry for now: use Manage with a bot."
)


def _is_futures_name(symbol: str) -> bool:
    return symbol.startswith("./") or symbol.startswith("/")


def _legs(body: dict, futures=None) -> tuple[list[Leg], int] | str:
    """The ticket's legs and quantity, or what's wrong with them. Shared with a ticket
    sent as a bot's entry (services/bot_ticket.py, bots slice 15). `futures`: how a
    futures ticket's DXLink names are resolved (bots slice 17); without it, refused."""
    found = ticket_legs(body, futures)
    return found if isinstance(found, str) else found[:2]


def ticket_legs(body: dict, futures=None) -> tuple[list[Leg], int, object] | str:
    """_legs, and for a futures ticket what its legs resolved to (bots/futures.Resolved;
    None for any other ticket). `futures(root, names)` raises futures.Unresolvable."""
    raw = body.get("legs")
    if not isinstance(raw, list) or not raw:
        return "Pick a bid (sell) or ask (buy) first."
    if not all(isinstance(item, dict) for item in raw):
        return "The ticket's legs aren't readable. Pick them again."
    names = [str(item.get("stream_symbol") or "") for item in raw]
    if any(_is_futures_name(name) for name in names):
        return _futures_legs(body, raw, names, futures)
    legs = []
    for item in raw:
        symbol = str(item.get("stream_symbol") or "")
        occ = occ_from_dxlink(symbol)
        if occ is None:
            return f"{symbol or 'A leg'} isn't an option the ticket knows."
        if not _whole(item.get("ratio")):
            return "Each leg needs a whole number of contracts."
        try:
            legs.append(Leg(occ, item.get("side"), item["ratio"]))
        except OrderError as e:
            return str(e)
    quantity = body.get("quantity")
    if not _whole(quantity) or quantity < 1:
        return "Set a whole number of contracts, at least 1."
    return legs, quantity, None


def _futures_legs(body: dict, raw: list, names: list[str], futures) -> tuple | str:
    """A futures ticket (bots slice 17): every leg a futures option of the page's root,
    each looked up in TastyTrade's series for its order symbol (futures_legs.resolve)."""
    if futures is None:
        return FUTURES_LATER
    if not all(name.startswith("./") for name in names):
        return "A ticket can't mix futures and stock options."
    root = body.get("ticker")
    if not isinstance(root, str) or not underlyings.futures(root):
        return "The ticket didn't say which futures it's on. Reload the page."
    if not all(_whole(item.get("ratio")) for item in raw):
        return "Each leg needs a whole number of contracts."
    try:
        resolved = futures(root, names)
    except futures_rules.Unresolvable as e:
        return str(e)
    legs = []
    for item, listed in zip(raw, resolved.legs, strict=True):
        try:
            legs.append(Leg(listed.symbol, item.get("side"), item["ratio"]))
        except OrderError as e:
            return str(e)
    quantity = body.get("quantity")
    if not _whole(quantity) or quantity < 1:
        return "Set a whole number of contracts, at least 1."
    return legs, quantity, resolved


def _ticket(body: dict, futures=None) -> tuple | str:
    """The ticket's legs, quantity, price, time in force and (a futures ticket) what it
    resolved to, or what's wrong with it. Needs no broker but a futures ticket's series
    (the analyzer's cache), so a bad ticket is turned away before anything is read."""
    found = ticket_legs(body, futures)
    if isinstance(found, str):
        return found
    legs, quantity, resolved = found
    raw_price = body.get("price")
    if type(raw_price) not in (int, float) or raw_price == 0 or not math.isfinite(raw_price):
        return "Set a limit price above 0."
    price = Decimal(str(raw_price))
    tif = TIFS.get(str(body.get("tif") or ""))
    if tif is None:
        return "Pick Day, GTC or GTC overnight."
    sides = {leg.side for leg in legs}
    if sides == {Side.SELL} and price < 0:
        return "Selling only takes in a credit: switch the ticket to Credit."
    if sides == {Side.BUY} and price > 0:
        return "Buying only pays a debit: switch the ticket to Debit."
    return legs, quantity, price, tif, resolved


def _order(
    legs: list[Leg], quantity: int, price: Decimal, tif: TimeInForce, held: dict
) -> Order | str:
    """The order, or what's wrong with it given what's held."""
    effect = order_effect([(leg.symbol, leg.side) for leg in legs], held)
    if effect == "roll":
        return "Rolls aren't supported yet: close and open separately."
    try:
        return Order(tuple(legs), quantity, effect == "opening", price=price, time_in_force=tif)
    except OrderError as e:
        return str(e)


def _positions(client, account_number: str) -> dict | orders.Result:
    try:
        return client.option_positions(account_number)
    except (BrokerError, CredentialsUnavailable) as e:
        logger.warning("couldn't read positions for %s: %s", account_number[-4:], e)
        return _refuse("The app couldn't read your positions at the broker, so it can't "
                       "tell an opening order from a closing one. Try again.")  # fmt: skip


def review(
    session_factory, clients, broker: str, account_number: str, body: dict, now=None,
    futures=None,
) -> Reviewed:  # fmt: skip
    """Check and preview the ticket (never sends). A passing preview is kept
    under a one-time code for Confirm. `futures` resolves a futures ticket's legs (bots
    slice 17, bot_futures.ticket_resolver); its series' increments must load
    (futures_legs.ensure_ticks), and only a bot may send it (Confirm refuses)."""
    now = now or datetime.now(UTC)

    def refused(message: str) -> Reviewed:
        return Reviewed(_refuse(message), None, "")

    client = next((c for c in clients if getattr(c, "broker", None) == broker), None)
    if client is None:
        return refused("That broker isn't connected.")
    checked = _ticket(body if isinstance(body, dict) else {}, futures)
    if isinstance(checked, str):
        return refused(checked)
    legs, quantity, price, tif, resolved = checked
    if resolved is not None:
        from optionsjourney.services import bot_futures

        try:
            bot_futures.ensure_ticks(session_factory, clients, resolved, now)
        except futures_rules.Unresolvable as e:
            return refused(str(e))
    held = _positions(client, account_number)
    if isinstance(held, orders.Result):
        return Reviewed(held, None, "")
    order = _order(legs, quantity, price, tif, held)
    if isinstance(order, str):
        return refused(order)
    try:  # on tick now, so the order previewed, kept and summarized is the one sent
        order = ticks.rounded(order, orders.tick_tables(session_factory, clients, order, now))
    except OrderError as e:
        return refused(f"{e} (after rounding to the tick)")
    request = orders.Request(broker, account_number, "you", order, preview_only=True)
    result = orders.submit(session_factory, clients, request, now=now)
    if result.action == "preview" and result.ok:
        multiplier = resolved.multiplier if resolved is not None else None
        summary = _summary(order, multiplier)
        return Reviewed(
            result, _keep(broker, account_number, order, now), summary, resolved is not None
        )
    return Reviewed(result, None, "")


# SAFETY: the only live send for origin "you": a stored, reviewed order, used once.
def confirm(
    session_factory, clients, broker: str, account_number: str, code, now=None, notify=None
) -> orders.Result:
    now = now or datetime.now(UTC)
    order = _take(code, broker, account_number, now)  # used up before anything is sent
    if order is None:
        return _refuse("That review has already been used or has expired. Review again.")
    if order.legs[0].futures:  # bots slice 17: plain futures orders come later (#93)
        return _refuse(FUTURES_TO_A_BOT)
    client = next((c for c in clients if getattr(c, "broker", None) == broker), None)
    if client is None:
        return _refuse("That broker isn't connected.")
    held = _positions(client, account_number)
    if isinstance(held, orders.Result):
        return held
    effect = order_effect([(leg.symbol, leg.side) for leg in order.legs], held)
    if effect == "roll" or (effect == "opening") != order.opening:
        return _refuse("Your positions changed since the review. Review again.")
    request = orders.Request(broker, account_number, "you", order)
    return orders.submit(session_factory, clients, request, now=now, notify=notify)
