"""Sending one bot order and chasing it (Tracker2's execution.chase_entry):
send through the order path, then every interval read it; while it's still
working, replace it one step further toward a fill; after the last try,
cancel and confirm the cancel. Never retried after a refusal; an unclear
send stops the chase and is left to recovery. Only a real send or replace
counts: a preview answer placed nothing (the bot went to Preview mid-chase).
An exit's first order the broker cancels at once is sent again, once, and a
dead replacement's old order is read again for its fill (decision 0085)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

from optionsjourney.bots.definition import ChaseDef
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_now
from optionsjourney.services import orders
from optionsjourney.trading import ticks
from optionsjourney.trading.orders import (
    BrokerOrder,
    OrderStatus,
    OrderType,
    filled_units,
    plain,
)

logger = logging.getLogger(__name__)
CONFIRM_READS = 3  # a cancel is read back up to 3 times, 1 s apart, before it counts as live


@dataclass
class Chased:
    # filled, partial, missed, refused, unclear, preview, or stopped (asked to stop:
    # its order cancelled and the cancel confirmed; `order` shows what filled)
    status: str
    order: BrokerOrder | None = None
    log: list[dict] = field(default_factory=list)
    message: str = ""
    # Refused by the broker on the first send (action "send"), so nothing is out;
    # a central-check refusal ("refuse") stays final.
    broker_refused: bool = False


def _filled_part(order: BrokerOrder | None) -> bool:
    return order is not None and any(leg.remaining < leg.quantity for leg in order.legs)


def read_after_cancel(deps, read: Callable[[], BrokerOrder]) -> BrokerOrder:
    """An order read back after its cancel: up to CONFIRM_READS reads, 1 s apart,
    while it still shows working (a cancel can take a moment to land)."""
    found = read()
    for _ in range(CONFIRM_READS - 1):
        if not found.may_be_working:
            break
        deps.sleep(1)
        found = read()
    return found


def record_order(on_order, order_id: str) -> str:
    """Tell `on_order` of an order, trying twice. "" once it's recorded, else why not
    (a database hiccup must never leave an order out there that nobody knows of)."""
    if on_order is None:
        return ""
    for attempt in range(2):
        try:
            on_order(order_id)
            return ""
        except Exception as e:  # noqa: BLE001 -- tried again, then the order is cancelled
            logger.exception("couldn't record order %s (try %s)", order_id, attempt + 1)
            why = e.__class__.__name__
    return f"Stopped chasing: couldn't record the order ({why})."


def _cancelled_at_once(deps, request: orders.Request, found: BrokerOrder) -> bool:
    """The chase's first order, on its first read, came back cancelled by the broker
    with nothing filled (Tracker2's exit_chase._may_retry, e7ab1a95): not rejected, not
    replaced from the broker app, not a market order, and only while the order's
    session is open. Nobody here cancels an order without reading it first (a stop
    request is checked before the read), so it was the broker's doing. The caller sends
    it again, once."""
    symbol = request.order.legs[0].symbol
    session = session_now(symbol, deps.now().astimezone(MARKET_TZ))
    return (
        found.status is OrderStatus.CANCELLED
        and not _filled_part(found)
        and not found.replaced_by
        and request.order.order_type is not OrderType.MARKET
        and session in ("regular", "futures")
    )


def _replaced_fill(client, request: orders.Request, old_id: str | None) -> BrokerOrder | None:
    """The order a dead, unfilled replacement replaced, read again (read-only;
    Tracker2's exit_chase._replaced_order_fill, 8dce236f): TastyTrade accepts a replace,
    then rejects the new order ("Received a fill on the original order") when the old
    one filled first. The old order when it filled, in whole or in part, and is done;
    else None (also when it can't be read: the chase ends as it would have)."""
    if old_id is None:
        return None
    try:
        old = client.get_order(request.account_number, old_id)
    except Exception:  # noqa: BLE001 -- a read-only second look; the chase's outcome stands
        logger.exception("couldn't read replaced order %s again", old_id)
        return None
    if old.status is OrderStatus.FILLED or (_filled_part(old) and not old.may_be_working):
        logger.info("replacement died unfilled; replaced order %s had filled", old_id)
        return old
    return None


def _asked(should_stop) -> bool:
    """Whether the chase was asked to stop. A check that breaks counts as not asked:
    the chase carries on, as it would have without the check."""
    if should_stop is None:
        return False
    try:
        return bool(should_stop())
    except Exception:  # noqa: BLE001 -- a hiccup is not a stop request
        logger.exception("couldn't check for a stop request")
        return False


def chase(
    deps,
    request: orders.Request,
    first: Decimal,
    plan: ChaseDef,
    next_price: Callable[[Decimal, Decimal], Decimal | None],
    on_order: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Chased:
    """`on_order(id)` hears of each order the chase puts out, the send and every
    replace, before any wait (Tracker2's on_order_id), so a crash never loses it.
    `should_stop()` is asked after every wait (the cancel button): when it says yes
    the order is cancelled, the cancel confirmed, and the chase ends "stopped" (or
    "filled", if it filled first). A next price equal to the current one keeps the
    order as it is for another interval."""
    client = next(c for c in deps.clients() if c.broker == request.broker)
    price = first
    result = orders.submit(
        deps.session_factory,
        deps.clients(),
        replace(request, order=replace(request.order, price=price)),
        now=deps.now(),
        notify=deps.notify,
    )
    log = [{"at": deps.now().isoformat(), "price": str(price), "result": result.message}]
    if result.action == "preview":
        return Chased("preview", None, log, result.message)
    if not (result.ok and result.action == "send"):
        unclear = result.message.startswith(orders.UNCLEAR)
        broker_refused = not unclear and result.action == "send"
        return Chased(
            "unclear" if unclear else "refused", None, log, result.message, broker_refused
        )
    order_id = result.broker_order_id
    replaced_id = None  # the order order_id replaced; None after a send
    found = None
    stopped = ""  # why the chase stopped early, when an order was refused
    asked = False  # should_stop said yes
    retried = False  # the one send again after a broker cancel at once is spent
    try:
        stopped = record_order(on_order, order_id)
        tries = 0 if stopped else plan.max_tries
        attempt = reads = 0
        while attempt < tries:
            deps.sleep(plan.interval_seconds)
            if _asked(should_stop):
                asked = True
                log.append({"at": deps.now().isoformat(), "event": "asked to stop"})
                break
            found = client.get_order(request.account_number, order_id)
            reads += 1
            if found.status is OrderStatus.FILLED:
                return Chased("filled", found, log)
            if reads == 1 and not retried and _cancelled_at_once(deps, request, found):
                # Sent again once, at the same price, through the central check; its
                # read doesn't use up a try (Tracker2's _retry).
                retried = True
                again = orders.submit(
                    deps.session_factory,
                    deps.clients(),
                    replace(request, order=replace(request.order, price=price)),
                    now=deps.now(),
                    notify=deps.notify,
                )
                log.append({
                    "at": deps.now().isoformat(), "price": str(price), "result": again.message,
                    "event": f"the broker cancelled {order_id} at once; sent it again",
                })  # fmt: skip
                if not (again.ok and again.action == "send"):
                    if again.message.startswith(orders.UNCLEAR):
                        return Chased("unclear", None, log, again.message)
                    # Nothing went out: the cancelled order is the chase's last.
                    return Chased("missed", found, log, f"Stopped chasing: {again.message}")
                order_id, found = again.broker_order_id, None
                stopped = record_order(on_order, order_id)
                if stopped:
                    break  # the new order is cancelled below, like any other
                continue
            # Gone, or partly filled: never replace a partly filled order (the
            # replace would be at the full size). Cancel it and keep what filled.
            if not found.may_be_working or _filled_part(found):
                if not found.may_be_working and not _filled_part(found):
                    old = _replaced_fill(client, request, replaced_id)
                    if old is not None:
                        log.append({
                            "at": deps.now().isoformat(), "order": replaced_id,
                            "event": f"replacement {order_id} died; the order it replaced filled",
                        })  # fmt: skip
                        if old.status is OrderStatus.FILLED:
                            return Chased("filled", old, log)
                        found = old
                break
            if attempt == plan.max_tries - 1:
                break
            lower = next_price(price, plan.step)
            if lower is None:
                break
            if lower == price:
                attempt += 1
                continue  # nothing to move to: the order keeps working as it is
            moved = orders.replace(
                deps.session_factory,
                deps.clients(),
                replace(request, order=replace(request.order, price=lower)),
                order_id,
                now=deps.now(),
                notify=deps.notify,
            )
            log.append({"at": deps.now().isoformat(), "price": str(lower), "result": moved.message})
            if not (moved.ok and moved.action == "replace"):
                # Refused, or only previewed: nothing replaced the working order.
                if moved.message.startswith(orders.UNCLEAR):
                    # The replacement may be at the broker under an id we don't have.
                    return Chased("unclear", None, log, moved.message)
                stopped = f"Stopped chasing: {moved.message}"
                break
            new_id = moved.broker_order_id or order_id
            replaced_id = order_id if new_id != order_id else replaced_id
            price, order_id = lower, new_id
            stopped = record_order(on_order, order_id)
            if stopped:
                break  # the new order is cancelled below, like any other
            attempt += 1
        if found is None or found.may_be_working or stopped:
            orders.cancel(
                deps.session_factory,
                deps.clients(),
                request.broker,
                request.account_number,
                order_id,
                request.origin,
                request.bot_id,
                now=deps.now(),
                notify=deps.notify,
            )
            found = read_after_cancel(
                deps, lambda: client.get_order(request.account_number, order_id)
            )
            log.append(
                {"at": deps.now().isoformat(), "cancel": order_id, "status": str(found.status)}
            )
            if found.may_be_working:
                return Chased(
                    "unclear", found, log, "The cancel isn't confirmed. Check your broker."
                )
    except Exception as e:  # noqa: BLE001 -- an order is out there; never lose track of it
        log.append({"at": deps.now().isoformat(), "order": order_id, "error": e.__class__.__name__})
        return Chased(
            "unclear",
            None,
            log,
            f"Couldn't read the order back ({e.__class__.__name__}). Check your broker.",
        )
    if found.status is OrderStatus.FILLED:
        return Chased("filled", found, log)
    if asked:
        return Chased("stopped", found, log, "Cancelled by you.")
    if _filled_part(found):
        return Chased("partial", found, log)
    return Chased("missed", found, log, stopped)


@dataclass
class Entered:
    """How an entry chase ended (Tracker2's ChaseResult with its tranches).
    `fills` are the (run id, order) of every tranche taken, already recorded by
    `on_fill`; their exits are the caller's to place, after the chase. `run_id`
    is the run left without a fill that still needs its ending (missed,
    unclear, ...), or None when every run holds a fill. `starts` is where each
    run's part of the log begins."""

    status: str  # filled, short, missed, refused, unclear, uneven, preview
    fills: list[tuple[int, BrokerOrder]] = field(default_factory=list)
    run_id: int | None = None
    order: BrokerOrder | None = None  # that run's order, as last read
    log: list[dict] = field(default_factory=list)
    message: str = ""
    requested: int = 0
    filled: int = 0  # units, across every tranche
    starts: dict[int, int] = field(default_factory=dict)

    def log_of(self, run_id: int) -> list[dict]:
        """A tranche run's own part of the log (the root gets the whole log)."""
        return self.log[self.starts.get(run_id, 0) :]


def chase_entry(
    deps,
    request: orders.Request,
    first: Decimal,
    plan: ChaseDef,
    next_price: Callable[[Decimal, Decimal], Decimal | None],
    on_tranche: Callable[[int, int], int],
    on_fill: Callable[[int, BrokerOrder], None],
) -> Entered:
    """Chase an entry in units (Tracker2's execution.chase_entry). A partly filled
    order is never replaced: it's cancelled, the cancel confirmed, and what filled
    becomes a tranche of the run it was sent under (`on_fill` records it at once).
    Exits are not placed here: like Tracker2's orchestrator, the caller places
    them for every filled run after the chase. If units and tries remain,
    `on_tranche(n, rest)` makes the next run and a fresh order for the rest goes
    out under it, at the current price, on the same attempt counter. Legs that
    filled without one whole unit end the chase as "uneven": nothing more is
    sent, and that run is left for you to settle. With `plan.window_minutes` (bots slice
    19, entry_window_minutes) the chase also stops once that long has passed since it
    began, checked after each read as Tracker2 does: cancelled, confirmed, any fill kept."""
    client = next(c for c in deps.clients() if c.broker == request.broker)
    window = plan.window_minutes
    deadline = deps.now() + timedelta(minutes=window) if window else None
    out = Entered("missed", requested=request.order.quantity)
    log = out.log
    price, size, tranche = first, request.order.quantity, 1
    run_id = request.bot_run_id  # the run the working order belongs to; None once it took a fill
    out.starts[run_id] = 0
    ratios = {plain(leg.symbol): leg.ratio for leg in request.order.legs}
    tables = orders.tick_tables(deps.session_factory, deps.clients(), request.order, deps.now())

    def units_of(found: BrokerOrder) -> int:
        if found.status is OrderStatus.FILLED:
            return size  # Tracker2's _count: a filled order filled all of it
        return min(size, filled_units(found, ratios)[0])

    def ask(units: int, at: Decimal) -> orders.Request:
        return replace(
            request, bot_run_id=run_id, order=replace(request.order, quantity=units, price=at)
        )

    def ended(status: str, order=None, message: str = "") -> Entered:
        out.status, out.run_id, out.order, out.message = status, run_id, order, message
        return out

    def take(found: BrokerOrder, units: int) -> None:
        nonlocal run_id
        on_fill(run_id, found)  # recorded now: a later failure can't lose it
        out.fills.append((run_id, found))
        out.filled += units
        log.append({"at": deps.now().isoformat(), "tranche": tranche, "filled": units})
        run_id = None

    def cancel_and_read(order_id: str) -> BrokerOrder:
        orders.cancel(
            deps.session_factory,
            deps.clients(),
            request.broker,
            request.account_number,
            order_id,
            request.origin,
            request.bot_id,
            now=deps.now(),
            notify=deps.notify,
        )
        found = client.get_order(request.account_number, order_id)
        log.append({"at": deps.now().isoformat(), "cancel": order_id, "status": str(found.status)})
        return found

    result = orders.submit(
        deps.session_factory, deps.clients(), ask(size, price), now=deps.now(), notify=deps.notify
    )
    log.append({"at": deps.now().isoformat(), "price": str(price), "result": result.message})
    if result.action == "preview":
        return ended("preview", message=result.message)
    if not (result.ok and result.action == "send"):
        unclear = result.message.startswith(orders.UNCLEAR)
        return ended("unclear" if unclear else "refused", message=result.message)
    order_id = result.broker_order_id
    found = None
    stopped = ""  # why the chase stopped early, when an order was refused
    unconfirmed = "The cancel isn't confirmed. Check your broker."
    uneven = "The entry's legs filled unevenly — settle it at your broker."
    try:
        for attempt in range(plan.max_tries):
            deps.sleep(plan.interval_seconds)
            found = client.get_order(request.account_number, order_id)
            if found.status is OrderStatus.FILLED or _filled_part(found):
                # Never replace a partly filled order (the replace would be at the full
                # size): cancel it, confirm, and take what filled as a tranche.
                working = found.may_be_working
                if working:
                    found = cancel_and_read(order_id)
                    if found.may_be_working:
                        return ended("unclear", found, unconfirmed)
                units = units_of(found)
                if not units:
                    return ended("uneven", found, uneven)
                take(found, units)
                order_id, size, found = None, size - units, None
                if not size:
                    return ended("filled")
                if not working or attempt == plan.max_tries - 1:
                    break  # gone at the broker after a fill, or out of tries
                if deadline is not None and deps.now() >= deadline:  # before the rest
                    stopped = f"Stopped: the {window}-minute entry window closed."
                    log.append({"at": deps.now().isoformat(), "event": "window closed"})
                    break
                tranche += 1
                run_id = on_tranche(tranche, size)
                out.starts[run_id] = len(log)
                result = orders.submit(
                    deps.session_factory,
                    deps.clients(),
                    ask(size, price),
                    now=deps.now(),
                    notify=deps.notify,
                )
                log.append(
                    {
                        "at": deps.now().isoformat(),
                        "tranche": tranche,
                        "contracts": size,
                        "price": str(price),
                        "result": result.message,
                    }
                )
                if not (result.ok and result.action == "send"):
                    if result.message.startswith(orders.UNCLEAR):
                        return ended("unclear", message=result.message)
                    stopped = f"Stopped chasing: {result.message}"
                    break  # refused, or only previewed: nothing was placed for the rest
                order_id = result.broker_order_id
                continue
            if not found.may_be_working:
                break  # gone at the broker, nothing filled
            if deadline is not None and deps.now() >= deadline:
                stopped = f"Stopped: the {window}-minute entry window closed."
                log.append({"at": deps.now().isoformat(), "event": "window closed"})
                break
            if attempt == plan.max_tries - 1:
                break
            # A step under the tick would round to the price already out there
            # (Tracker2's execution._concede): step by at least the tick.
            tick = ticks.order_tick(request.order, price, tables)
            lower = next_price(price, max(plan.step, tick))
            if lower is None:
                break
            moved = orders.replace(
                deps.session_factory,
                deps.clients(),
                ask(size, lower),
                order_id,
                now=deps.now(),
                notify=deps.notify,
            )
            log.append({"at": deps.now().isoformat(), "price": str(lower), "result": moved.message})
            if not (moved.ok and moved.action == "replace"):
                # Refused, or only previewed: nothing replaced the working order.
                if moved.message.startswith(orders.UNCLEAR):
                    # The replacement may be at the broker under an id we don't have.
                    return ended("unclear", message=moved.message)
                stopped = f"Stopped chasing: {moved.message}"
                break
            price, order_id = lower, moved.broker_order_id or order_id
        if order_id is not None:
            if found is None or found.may_be_working:
                found = cancel_and_read(order_id)
                if found.may_be_working:
                    return ended("unclear", found, unconfirmed)
            if found.status is OrderStatus.FILLED or _filled_part(found):
                # It filled, or part filled, before the cancel (or the refused replace).
                units = units_of(found)
                if not units:
                    return ended("uneven", found, uneven)
                take(found, units)
                size, found = size - units, None
    except Exception as e:  # noqa: BLE001 -- an order is out there; never lose track of it
        log.append({"at": deps.now().isoformat(), "order": order_id, "error": e.__class__.__name__})
        what = f"({e.__class__.__name__}). Check your broker."
        if run_id is None:  # between tranches: every order so far is settled
            return ended("short", message=f"Stopped after a tranche {what}")
        return ended("unclear", message=f"Couldn't read the order back {what}")
    if not size:
        return ended("filled")
    return ended("short" if out.fills else "missed", found, stopped)
