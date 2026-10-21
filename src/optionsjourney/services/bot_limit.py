"""A bot's resting entry: one limit that rests at the broker until it fills (spec: bots
slice 17, section 5; Tracker2's services/bots/analyzer_entry.py: place_limit_entry,
check_working_entries, _handle, _hold_partial, _replace_remainder, cancel_entry).

A futures ticket sent from the Trade page is entered this way, never chased ("Futures
trade as a Limit only"): the reviewed price and time in force, through the one order
path. The run is "working" while its order rests; recovery's reconcile reads only
"running" runs, so a resting entry is never cancelled as an orphan. The monitor's pass
reads each working order at every hour: a fill is recorded and handed to the exits (a
futures run's profit target only), a partial fill is given 60 seconds in the regular
session and then settled, with the rest placed again as its own run, and an order that
ended unfilled ends the run with an alert."""

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from optionsjourney.bots import futures
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_now
from optionsjourney.db.models import BotRun, OrderLog
from optionsjourney.services import bot_entry, bot_exits, bot_store, orders
from optionsjourney.services.bot_chase import read_after_cancel
from optionsjourney.trading.orders import (
    Leg,
    Order,
    OrderStatus,
    TimeInForce,
    filled_units,
    plain,
)

logger = logging.getLogger(__name__)
WORKING = "working"  # bot_runs.status of a resting entry
PARTIAL_GRACE = timedelta(seconds=60)  # PARTIAL_FILL_GRACE_SECONDS
LOCK_WAIT = 30  # seconds Cancel entry waits for the monitor's pass (LOCK_TIMEOUT_SECONDS)
NOT_FOLLOWED = (
    "TastyTrade named no order replacing it, so the bot doesn't follow a new one: if you "
    "placed one yourself, hand the trade to the bot from the journal once it fills."
)

MAX_CANCELS = 2  # bot_loops.MAX_CANCELS: cancels of one partly filled entry, then you
UNREADABLE_PASSES = 10  # passes (5 minutes) an entry may stay unreadable before an alert
_unreadable: dict[int, int] = {}  # run id -> passes in a row its order couldn't be read

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(bot_id: int) -> threading.Lock:
    """One lock per bot (exit_monitor.bot_lock): the monitor's pass and Cancel entry never
    both reach a fill of the same run."""
    with _locks_guard:
        return _locks.setdefault(bot_id, threading.Lock())


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str


def _alert(deps, bot, title: str, detail: str, run_id: int, routine: bool = False) -> None:
    bot_entry.alert(deps, f"{bot.name}: {title}", detail, f"/automation/runs/{run_id}",
                    run_id=run_id, bot_id=bot.id, routine=routine)  # fmt: skip


def _log(deps, run_id: int, **entry) -> None:
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        run.chase_log = [*(run.chase_log or []), {"at": deps.now().isoformat(), **entry}]
        run.updated_at = deps.now()
        session.commit()


def _price_words(price: Decimal, tif) -> str:
    kind = "credit" if price > 0 else "debit"
    return f"{abs(price):f} {kind}, {bot_entry_tif(tif)}"


def bot_entry_tif(tif) -> str:
    from optionsjourney.services.trade_ticket import TIF_WORDS

    return TIF_WORDS.get(tif, str(tif))


# --- placing it ----------------------------------------------------------------------------


def run_legs(pairs, resolved: futures.Resolved, today) -> list[dict]:
    """The run's legs, in the bot's leg order (bot_entry's legs JSON), each with what its
    series said (futures_legs.resolve: symbol, streamer symbol and the exact expires_at).
    Their mids aren't read: a resting entry is priced by you."""
    found = {plain(leg.symbol): leg for leg in resolved.legs}
    out = []
    for held, b in sorted(pairs, key=lambda p: p[1].position):
        listed = found[plain(held.symbol)]
        out.append({
            "position": b.position, "symbol": held.symbol,
            "strike": f"{held.strike.normalize():f}", "right": held.right,
            "side": str(held.side), "ratio": b.ratio, "group": b.group, "mid": "—",
            "expiration": held.expiration.isoformat(), "dte": (held.expiration - today).days,
            "expires_at": listed.expires_at.isoformat() if listed.expires_at else None,
            "stream_symbol": listed.stream_symbol,
        })  # fmt: skip
    return out


def entry_order(pairs, units: int, price: Decimal, tif) -> Order:
    """The bot's opening limit: its legs at their ratios, `units` of it, the reviewed
    price and time in force (_limit_order)."""
    legs = tuple(
        Leg(held.symbol, held.side, b.ratio)
        for held, b in sorted(pairs, key=lambda p: p[1].position)
    )
    return Order(legs, units, True, price=price, time_in_force=tif)


def place(deps, bot, run_id: int, legs: list[dict], order: Order, resolved, lead: dict) -> None:
    """The claimed run's resting entry (place_limit_entry): the run's legs and futures
    facts first (run.sizing: multiplier, contract, pt_only), then one opening limit
    through the order path. Raises only before anything is sent."""
    bot_entry.update_run(
        deps, run_id, status="running" if bot.live else "pending", legs=legs,
        target_credit=order.price, contracts=order.quantity, multiplier=resolved.multiplier,
        contract=resolved.contract, pt_only=True, entry_mode="limit", chase_log=[lead],
    )  # fmt: skip
    _send(deps, bot, run_id, order)


def _send(deps, bot, run_id: int, order: Order) -> None:
    request = orders.Request(
        bot.broker, bot.account_number, "bot", order, bot_id=bot.id, bot_run_id=run_id,
        preview_only=not bot.live,  # a bot switched to preview only previews
    )  # fmt: skip
    result = orders.submit(deps.session_factory, deps.clients(), request, now=deps.now(),
                           notify=deps.notify)  # fmt: skip
    words = _price_words(order.price, order.time_in_force)
    if result.action == "send" and result.ok:
        bot_entry.update_run(deps, run_id, status=WORKING, broker_order_id=result.broker_order_id)
        _log(deps, run_id, event="placed", price=f"{order.price:f}",
             order_id=result.broker_order_id, contracts=order.quantity,
             tif=str(order.time_in_force))  # fmt: skip
        _alert(deps, bot, "entry resting",
               f"Its entry rests at TastyTrade at {words} (order {result.broker_order_id}). "
               f"{bot_exits.FUTURES_NOTE}.", run_id, routine=True)  # fmt: skip
    elif result.action == "preview":
        bot_entry.update_run(deps, run_id, status="preview", reason=result.message)
    elif result.message.startswith(orders.UNCLEAR):
        detail = (
            f"{result.message} The app looks for the order at your broker: once it's found, it "
            "becomes the bot's resting entry, watched like any other, and a fill gets its profit "
            "target. Until then, check your broker."
        )
        bot_entry.update_run(deps, run_id, status="running", reason=detail,
                             broker_order_id=result.broker_order_id)  # fmt: skip
        _alert(deps, bot, "entry unclear", detail, run_id)
    else:
        bot_entry.update_run(deps, run_id, status="error", reason=result.message)
        _alert(deps, bot, "entry refused", result.message, run_id)


# --- the monitor's pass --------------------------------------------------------------------


def check_pass(deps) -> None:
    """Every working entry (check_working_entries), at every hour. Nothing while Freeze
    is on: a fill found then couldn't get its profit target. A bot busy with Cancel
    entry is read next pass."""
    if orders.switches(deps.session_factory).freeze:
        return
    with deps.session_factory() as session:
        working = [
            (r.id, r.bot_id)
            for r in session.scalars(select(BotRun).where(BotRun.status == WORKING))
        ]
    for run_id, bot_id in working:
        if run_id in bot_entry.ACTIVE:
            continue
        lock = _lock(bot_id)
        if not lock.acquire(blocking=False):
            continue
        try:
            _check(deps, run_id, hold=True)
            if _unreadable.pop(run_id, 0) >= UNREADABLE_PASSES:
                _read_again(deps, run_id)
        except Exception:  # noqa: BLE001 -- one run never stops the others
            logger.exception("couldn't check resting entry %s", run_id)
            _not_read(deps, run_id, bot_id)
        finally:
            lock.release()


UNREAD = "The app can't read this resting entry at your broker."


def _read_again(deps, run_id: int) -> None:
    """Its order reads again: the Needs-you mark _not_read wrote comes off (only that one)."""
    with deps.session_factory() as session:
        reason = session.get(BotRun, run_id).reason or ""
    if reason.startswith(f"{bot_entry.NEEDS_YOU}: {UNREAD}"):
        bot_entry.update_run(deps, run_id, reason="Read at your broker again.")


def _not_read(deps, run_id: int, bot_id: int) -> None:
    """A resting entry that couldn't be read UNREADABLE_PASSES passes in a row is said
    once: it may have filled with no profit target placed."""
    _unreadable[run_id] = _unreadable.get(run_id, 0) + 1
    if _unreadable[run_id] != UNREADABLE_PASSES:
        return
    bot = bot_store.load(deps.session_factory, bot_id)
    detail = (
        f"{UNREAD} If it has filled, its profit target isn't placed yet. Check your broker; "
        "the app keeps trying."
    )
    bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    _alert(deps, bot, "resting entry can't be read", detail, run_id)


def _facts(deps, run_id: int):
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or run.status != WORKING or not run.broker_order_id:
            return None
        return (run.bot_id, run.broker, run.account_number, run.broker_order_id, run.contracts,
                {plain(leg["symbol"]): leg["ratio"] for leg in run.legs})  # fmt: skip


def _check(deps, run_id: int, hold: bool, cancelled_by_app: bool = False, found=None,
           hops: int = 0) -> str:  # fmt: skip
    facts = _facts(deps, run_id)
    if facts is None:
        return "gone"
    bot_id, broker, account, order_id, wanted, ratios = facts
    bot = bot_store.load(deps.session_factory, bot_id)
    client = bot_exits._client(deps, broker)
    if found is None:
        found = client.get_order(account, order_id)
    units, uneven = filled_units(found, ratios)
    if found.status is OrderStatus.FILLED or units >= wanted:
        return _filled(deps, bot, run_id, found, uneven)
    if units and found.may_be_working:
        if hold and _hold_partial(deps, run_id):
            return WORKING  # give the rest a moment to fill
        if _cancels_sent(deps, account, order_id) >= MAX_CANCELS:
            _stuck(deps, bot, run_id)
            return WORKING  # still read every pass: it may fill yet
        # Noted before the cancel goes out (_mark_cancel_requested), so a later pass that
        # finds it cancelled with units still wanted places the rest again, a crash between
        # here and the fill included.
        _log(deps, run_id, event="cancel_requested", reason="partial")
        result = orders.cancel(deps.session_factory, deps.clients(), broker, account, order_id,
                               "bot", bot_id, now=deps.now(), notify=deps.notify)  # fmt: skip
        if not result.ok and not result.message.startswith(orders.UNCLEAR):
            # Plainly refused: nothing was asked of the broker, so the mark doesn't stand
            # (a cancel you then make at the broker must not place the rest again).
            _log(deps, run_id, event="cancel_failed", reason=result.message)
        found = read_after_cancel(deps, lambda: client.get_order(account, order_id))
        if found.may_be_working:
            return WORKING  # the cancel isn't confirmed: tried again next pass
        units, uneven = filled_units(found, ratios)
        if units >= wanted:
            return _filled(deps, bot, run_id, found, uneven)
        return _partial(deps, bot, run_id, found, units, wanted, not cancelled_by_app)
    if units and not cancelled_by_app and found.replaced_by:
        # Bots slice 21 (_follow_successor: "the replaced order has partial fills"): a
        # part-filled entry replaced at the broker is left to you; nothing is placed again.
        return _lost(deps, bot, run_id, found.broker_order_id,
                     f"it had filled {units} first, then order {found.replaced_by} replaced "
                     "it")  # fmt: skip
    if units:
        again = not cancelled_by_app and _cancel_requested(deps, run_id)
        return _partial(deps, bot, run_id, found, units, wanted, again)
    if any(leg.remaining < leg.quantity for leg in found.legs):  # contracts, not whole units
        detail = bot_entry.UNEVEN_DETAIL
        bot_entry.update_run(deps, run_id, status="running",
                             reason=f"{bot_entry.NEEDS_YOU}: {detail}")  # fmt: skip
        _alert(deps, bot, "a run needs you", detail, run_id)
        return "uneven"
    if found.may_be_working:
        return WORKING
    if not cancelled_by_app and found.replaced_by:  # bots slice 21: edited at the broker
        return _follow(deps, bot, run_id, found, hold, hops)
    return _ended(deps, bot, run_id, found.status, cancelled_by_app)


def _cancels_sent(deps, account: str, order_id: str) -> int:
    """The app's own cancels of this order, from the order log (bot_loops._cancels_sent)."""
    with deps.session_factory() as session:
        return session.scalar(
            select(func.count(OrderLog.id)).where(
                OrderLog.account_number == account,
                OrderLog.action == "cancel",
                OrderLog.broker_order_id == order_id,
            )
        )


def _stuck(deps, bot, run_id: int) -> None:
    """A partly filled entry still working after MAX_CANCELS cancels: said once (the
    run's reason is marked), and left to you; the filled part isn't protected yet."""
    with deps.session_factory() as session:
        flagged = (session.get(BotRun, run_id).reason or "").startswith(bot_entry.NEEDS_YOU)
    if flagged:
        return
    detail = (
        "Part of this entry filled, and the rest is still working at your broker after the "
        f"app cancelled it {MAX_CANCELS} times. The filled part has no profit target yet. "
        "Cancel the rest at your broker; the app then protects what filled."
    )
    bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    _alert(deps, bot, "a run needs you", detail, run_id)


def _hold_partial(deps, run_id: int) -> bool:
    """A partly filled limit stays working PARTIAL_GRACE from the first pass that saw it,
    only while the regular session is open for the whole hold (_hold_partial); the time
    is kept in the run's log, so a restart keeps the clock."""
    now = deps.now()
    local = now.astimezone(MARKET_TZ)
    if (
        session_now("SPY", local) != "regular"
        or session_now("SPY", (now + PARTIAL_GRACE).astimezone(MARKET_TZ)) != "regular"
    ):
        return False
    with deps.session_factory() as session:
        log = list(session.get(BotRun, run_id).chase_log or [])
    seen = next((e["seen_at"] for e in log if e.get("event") == "partial_seen"), None)
    if seen is None:
        _log(deps, run_id, event="partial_seen", seen_at=now.isoformat())
        return True
    from datetime import datetime

    return now - datetime.fromisoformat(seen) < PARTIAL_GRACE


def _cancel_requested(deps, run_id: int) -> bool:
    """This app cancelled the rest of a partial fill (its "cancel_requested" mark, written
    before the cancel goes out), so the rest is placed again; never after Cancel entry."""
    with deps.session_factory() as session:
        log = session.get(BotRun, run_id).chase_log or []
    asked = False
    for e in log:  # the latest word wins: a refused cancel takes its mark back
        if e.get("event") == "cancel_requested" and e.get("reason") == "partial":
            asked = True
        elif e.get("event") == "cancel_failed":
            asked = False
    return asked


def _filled(deps, bot, run_id: int, found, uneven: bool) -> str:
    bot_entry.record_fill(deps, run_id, found, "filled")
    _said_filled(deps, bot, run_id)
    bot_entry.hand_to_exits(deps, bot, run_id)
    return "filled"


def _said_filled(deps, bot, run_id: int) -> None:
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        credit, multiplier, contracts = run.fill_credit, run.multiplier, run.contracts
    kind = "credit" if (credit or 0) > 0 else "debit"
    _alert(
        deps, bot, "entry filled",
        f"Filled {contracts} at {abs(credit or 0):f} {kind} (×{multiplier:f}: "
        f"{futures.dollars(credit, multiplier)} a contract). Its profit target is placed now. "
        f"{bot_exits.FUTURES_NOTE}.", run_id, routine=True,
    )  # fmt: skip


def _partial(deps, bot, run_id: int, found, units: int, wanted: int, again: bool) -> str:
    """The filled units recorded and protected; with `again`, the rest placed again as its
    own run at the same price (_replace_remainder)."""
    bot_entry.record_fill(deps, run_id, found, "filled")
    _said_filled(deps, bot, run_id)
    bot_entry.hand_to_exits(deps, bot, run_id)
    if again:
        _remainder(deps, bot, run_id, wanted - units)
    else:  # you cancelled it, here or at the broker: the rest is dropped, and said
        detail = (
            f"{wanted - units} contract(s) of the entry weren't placed again: the order was "
            "cancelled before they filled. What filled has its profit target."
        )
        _alert(deps, bot, "the rest of the entry wasn't placed", detail, run_id)
    return "partial"


def _remainder(deps, bot, run_id: int, rest: int) -> None:
    """A child run for the units still wanted (the root's tranche, bot_entry._tranche_run),
    resting the root's limit, with the root's futures facts (REMAINDER_SIZING_KEYS)."""
    with deps.session_factory() as session:
        root = session.get(BotRun, run_id)
        root_id = root.parent_run_id or root.id
        top = session.get(BotRun, root_id)
        index = 2 + session.scalar(
            select(func.count(BotRun.id)).where(BotRun.parent_run_id == root_id)
        )
        price, legs = top.target_credit, list(root.legs)
        facts = {"multiplier": top.multiplier, "contract": top.contract, "pt_only": True,
                 "entry_mode": "limit"}  # fmt: skip
        tif = _tif_of(top.chase_log)
    owned: list[int] = []
    child = bot_entry._tranche_run(deps, root_id, index, rest, owned)
    try:
        bot_entry.update_run(deps, child, target_credit=price, **facts)
        _log(deps, child, event="remainder", of=run_id, contracts=rest)
        order = Order(
            tuple(Leg(leg["symbol"], leg["side"], leg["ratio"]) for leg in legs), rest, True,
            price=price, time_in_force=tif,
        )  # fmt: skip
        _send(deps, bot, child, order)
    except Exception:  # noqa: BLE001 -- a send that may be out leaves it for recovery
        logger.exception("couldn't place the remainder of run %s", run_id)
        bot_entry._failed(deps, child, RuntimeError("the remainder broke"))
        _alert(deps, bot, "the rest of the entry wasn't placed",
               f"{rest} contract(s) of the entry weren't placed again. Check your broker.",
               child)  # fmt: skip
    finally:
        with bot_entry._active_lock:
            bot_entry.ACTIVE.difference_update(owned)


def _tif_of(log) -> TimeInForce:
    """The root's time in force, from its "placed" entry (Tracker2's entry_tif, copied to
    the remainder); GTC if it can't be read."""
    found = next((e.get("tif") for e in log or () if e.get("event") == "placed"), None)
    try:
        return TimeInForce(found)
    except ValueError:
        return TimeInForce.GTC


def _ended(deps, bot, run_id: int, status, cancelled_by_app: bool) -> str:
    """The order ended with nothing filled (_handle's terminal branch). Bots slice 21: after
    a Modify that got no clear answer it's never a plain cancel: a replacement may rest."""
    if not cancelled_by_app and _modify_unclear(deps, run_id):
        return _lost(deps, bot, run_id, "", "a Modify got no clear answer, so a replacement "
                     "may rest at your broker under an order the app doesn't know")  # fmt: skip
    if cancelled_by_app:
        bot_entry.update_run(deps, run_id, status="cancelled", reason="You cancelled the entry.")
        return "cancelled"
    if status is OrderStatus.CANCELLED:
        reason = f"The entry order was cancelled at your broker. {NOT_FOLLOWED}"
        bot_entry.update_run(deps, run_id, status="cancelled", reason=reason)
        _alert(deps, bot, "entry cancelled", reason, run_id)
        return "cancelled"
    reason = f"The entry order {status} unfilled at your broker."
    bot_entry.update_run(deps, run_id, status="missed", reason=reason)
    _alert(deps, bot, "entry missed", reason, run_id)
    return "missed"


# --- Bots slice 21: following an entry replaced at the broker ------------------------------

MAX_HOPS = 3  # analyzer_entry.MAX_SUCCESSOR_HOPS: replacements followed in one pass
FOLLOWED = "followed"
MODIFY_UNCLEAR = "modify_unclear"


MODIFYING = "modifying"  # logged before a Modify's replace goes out
MODIFY_REFUSED = "modify_refused"  # its replace was refused: nothing changed


def _modify_unclear(deps, run_id: int) -> bool:
    """A Modify whose replace may have gone through without the app knowing its new order:
    it got no clear answer, or it was cut off after it was sent, and nothing since (the
    modify recorded, or the replacing order followed) settled it."""
    with deps.session_factory() as session:
        log = session.get(BotRun, run_id).chase_log or []
    unsure = False
    for e in log:
        if e.get("event") in (MODIFYING, MODIFY_UNCLEAR):
            unsure = True
        elif e.get("event") in ("modified", FOLLOWED, MODIFY_REFUSED):
            unsure = False
    return unsure


def _lost(deps, bot, run_id: int, order_id: str, why: str) -> str:
    """A successor may rest and can't be pinned down (analyzer_entry._lost_track): the app
    stops watching the run and tells you. Never a fill or a cancel it can't vouch for."""
    detail = (
        f"The entry{f' (order {order_id})' if order_id else ''} was replaced at your broker "
        f"and the app can't follow it: {why}. Check your broker: if it fills there, hand the "
        "trade to the bot from the journal."
    )
    bot_entry.update_run(deps, run_id, status="error", reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    _alert(deps, bot, "entry replaced at your broker: check it", detail, run_id)
    return "lost"


def _same_entry(deps, run_id: int, found) -> bool:
    """The replacing order is this entry's: the same legs, each opening the run's way, at
    the run's size and ratios."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        wanted = {(plain(leg["symbol"]), str(leg["side"])): run.contracts * int(leg["ratio"])
                  for leg in run.legs}  # fmt: skip
    theirs = {(plain(leg.symbol), str(leg.side)): leg.quantity for leg in found.legs}
    return all(leg.opening for leg in found.legs) and theirs == wanted


def _follow(deps, bot, run_id: int, found, hold: bool, hops: int) -> str:
    """The order that replaced the run's resting entry at the broker (Tracker2's
    _follow_successor, by TastyTrade's replacing order id): read, checked to be this
    entry's legs and size, then tracked as the run's order and read as any resting entry.
    At most MAX_HOPS a pass; anything it can't be sure of is left to you."""
    old_id, new_id = found.broker_order_id, found.replaced_by
    if hops >= MAX_HOPS:
        return _lost(deps, bot, run_id, old_id, f"more than {MAX_HOPS} replacements")
    facts = _facts(deps, run_id)
    if facts is None:
        return "gone"
    _, broker, account, _, _, _ = facts
    try:
        new = bot_exits._client(deps, broker).get_order(account, new_id)
    except Exception:  # noqa: BLE001 -- can't tell what rests: left to you
        logger.exception("couldn't read order %s replacing run %s's entry", new_id, run_id)
        return _lost(deps, bot, run_id, old_id, f"its replacing order {new_id} couldn't be read")
    if not _same_entry(deps, run_id, new):
        return _lost(deps, bot, run_id, old_id,
                     f"order {new_id} isn't this entry's legs and size")  # fmt: skip
    bot_entry.update_run(deps, run_id, broker_order_id=new_id)
    _log(deps, run_id, event=FOLLOWED, order_id=new_id, replaced_order_id=old_id)
    _alert(deps, bot, "entry replaced at your broker",
           f"The resting entry (order {old_id}) was replaced at your broker by order {new_id}; "
           "the bot follows the new one, and a fill gets its profit target.", run_id,
           routine=True)  # fmt: skip
    return _check(deps, run_id, hold, found=new, hops=hops + 1)


# --- Bots slice 21: Modify a resting entry -------------------------------------------------


def _new_price(text: str, placed: Decimal | None):
    """(the price signed like the entry, None) or (None, why): a positive number."""
    try:
        price = Decimal(str(text or "").strip())
    except ArithmeticError, ValueError:
        return None, "The new limit must be a number."
    if not price.is_finite() or price <= 0:
        return None, "The new limit must be a price above 0."
    return (price if (placed or 0) >= 0 else -price), None


def modify_entry(deps, run_id: int, text: str) -> Outcome:
    """Re-price a run's resting entry (or its resting remainder) in place (Tracker2's
    analyzer_entry.modify_entry), under the bot's lock, from a fresh read: filled or ended
    at the broker, it's handled as the monitor would and nothing is modified; partly
    filled, the fill is recorded and protected and the rest rests at the new price; else
    replaced through the order path (orders.replace: Freeze, No new trades and the size
    limits apply) and the new id tracked. The old order is read again after: a fill that
    landed first is never guessed at (Needs you)."""
    target = _resolve_working(deps, run_id)
    if target is None:
        return Outcome(False, "There's no resting entry on this run to modify.")
    with deps.session_factory() as session:
        run = session.get(BotRun, target)
        bot_id, placed = run.bot_id, run.target_credit
    price, problem = _new_price(text, placed)
    if problem:
        return Outcome(False, problem)
    lock = _lock(bot_id)
    if not lock.acquire(timeout=LOCK_WAIT):
        return Outcome(False, "The bot is busy; try again in a moment.")
    try:
        return _modify(deps, target, bot_id, price)
    finally:
        lock.release()


def _modify(deps, run_id: int, bot_id: int, price: Decimal) -> Outcome:
    from optionsjourney.trading import ticks

    facts = _facts(deps, run_id)
    if facts is None:
        return Outcome(False, "There's no resting entry on this run to modify.")
    _, broker, account, order_id, wanted, ratios = facts
    bot = bot_store.load(deps.session_factory, bot_id)
    client = bot_exits._client(deps, broker)
    found = client.get_order(account, order_id)
    units, _ = filled_units(found, ratios)
    if found.status is OrderStatus.FILLED or units >= wanted or not found.may_be_working:
        outcome = _check(deps, run_id, hold=False, found=found)
        return Outcome(False, f"Nothing was modified: at your broker the entry had {outcome} "
                              "first.")  # fmt: skip
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        legs = list(run.legs)
        root = session.get(BotRun, run.parent_run_id) if run.parent_run_id else run
        tif = _tif_of(root.chase_log)
    order = Order(tuple(Leg(leg["symbol"], leg["side"], leg["ratio"]) for leg in legs), wanted,
                  True, price=price, time_in_force=tif)  # fmt: skip
    try:
        tables = orders.tick_tables(deps.session_factory, deps.clients(), order, deps.now())
        order = ticks.rounded(order, tables)
    except Exception as e:  # noqa: BLE001 -- off the tick or no table: nothing sent
        return Outcome(False, f"The new limit can't be priced ({e}). Nothing was modified.")
    if units:  # partly filled: settled, the rest rests at the new price (remainder_price)
        bot_entry.update_run(deps, run_id, target_credit=order.price)
        outcome = _check(deps, run_id, hold=False, found=found)
        if outcome == WORKING:
            return Outcome(False, "Part of it filled; its cancel isn't confirmed yet. The rest "
                                  f"rests at {abs(order.price):f} once it is.")  # fmt: skip
        return Outcome(outcome == "partial", "Part of it had filled: that part keeps its "
                       f"profit target, and the rest rests at {abs(order.price):f}.")  # fmt: skip
    request = orders.Request(
        broker, account, "bot", order, bot_id=bot_id, bot_run_id=run_id,
        preview_only=not bot.live,
    )  # fmt: skip
    _log(deps, run_id, event=MODIFYING, price=f"{order.price:f}", order_id=order_id)
    result = orders.replace(deps.session_factory, deps.clients(), request, order_id,
                            now=deps.now(), notify=deps.notify)  # fmt: skip
    words = _price_words(order.price, tif)
    if result.action == "replace" and result.ok:
        bot_entry.update_run(deps, run_id, broker_order_id=result.broker_order_id,
                             target_credit=order.price)  # fmt: skip
        _log(deps, run_id, event="modified", price=f"{order.price:f}",
             order_id=result.broker_order_id, replaced_order_id=order_id)  # fmt: skip
        try:
            early, _ = filled_units(client.get_order(account, order_id), ratios)
        except Exception:  # noqa: BLE001 -- never assume nothing filled: you check
            logger.exception("couldn't read run %s's old entry after its modify", run_id)
            detail = (
                f"The entry was modified (now order {result.broker_order_id}), but the old "
                f"order {order_id} couldn't be read back, so it isn't known whether part of it "
                "filled first. Check your broker."
            )
            bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
            _alert(deps, bot, "entry modified: check it", detail, run_id)
            return Outcome(False, detail)
        if early:
            detail = (
                f"Order {order_id} filled {early} before the modify landed, and its replacement "
                f"{result.broker_order_id} rests for {wanted}: more could fill than you asked "
                "for, and the app stops watching this entry. Check your broker."
            )
            # _replace_working: "Never guess: flag it for a human" (run.error: not watched)
            bot_entry.update_run(deps, run_id, status="error",
                                 reason=f"{bot_entry.NEEDS_YOU}: {detail}")  # fmt: skip
            _alert(deps, bot, "entry filled before the modify landed", detail, run_id)
            return Outcome(False, detail)
        return Outcome(True, f"The entry rests at {words} now (order {result.broker_order_id}).")
    if result.message.startswith(orders.UNCLEAR):
        _log(deps, run_id, event=MODIFY_UNCLEAR, message=result.message)
        detail = (
            f"The modify got no clear answer ({result.message}). A replacement may rest at your "
            "broker; the app keeps reading the old order. Check your broker."
        )
        _alert(deps, bot, "entry modify unclear", detail, run_id)
        return Outcome(False, detail)
    _log(deps, run_id, event=MODIFY_REFUSED, result=result.message)  # nothing changed
    if result.action == "preview":
        return Outcome(False, "The bot is in preview now, so the entry wasn't modified.")
    return Outcome(False, f"Not modified: {result.message}")


# --- Cancel entry --------------------------------------------------------------------------


def _resolve_working(deps, run_id: int) -> int | None:
    """The run's own resting entry, or once a partial fill has finished it its latest
    resting remainder (_resolve_working)."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None:
            return None
        if run.status == WORKING:
            return run.id
        child = session.scalar(
            select(BotRun.id)
            .where(BotRun.parent_run_id == (run.parent_run_id or run.id),
                   BotRun.status == WORKING)
            .order_by(BotRun.id.desc())
        )  # fmt: skip
        return child


def cancel_entry(deps, run_id: int) -> Outcome:
    """Cancel a run's resting entry (cancel_entry): only a working one; the cancel read
    back; a fill found then is kept and protected, and the rest isn't placed again."""
    target = _resolve_working(deps, run_id)
    if target is None:
        return Outcome(False, "There's no resting entry on this run to cancel.")
    with deps.session_factory() as session:
        bot_id = session.get(BotRun, target).bot_id
    lock = _lock(bot_id)
    if not lock.acquire(timeout=LOCK_WAIT):
        return Outcome(False, "The bot is busy; try again in a moment.")
    try:
        facts = _facts(deps, target)
        if facts is None:
            return Outcome(False, "There's no resting entry on this run to cancel.")
        _, broker, account, order_id, _, _ = facts
        result = orders.cancel(deps.session_factory, deps.clients(), broker, account, order_id,
                               "bot", bot_id, now=deps.now(), notify=deps.notify)  # fmt: skip
        client = bot_exits._client(deps, broker)
        found = read_after_cancel(deps, lambda: client.get_order(account, order_id))
        if found.may_be_working:
            why = "" if result.ok else f" ({result.message})"
            return Outcome(False, f"The cancel isn't confirmed yet{why}; check your broker.")
        outcome = _check(deps, target, hold=False, cancelled_by_app=True, found=found)
    finally:
        lock.release()
    if outcome == "cancelled":
        return Outcome(True, "The entry was cancelled; nothing filled.")
    if outcome in ("filled", "partial"):
        return Outcome(True, "The entry was cancelled; what had filled keeps its profit target.")
    return Outcome(False, "Check the run: the broker's answer wasn't a plain cancel.")
