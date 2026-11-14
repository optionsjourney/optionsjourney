"""The Dynamic stop's ratchet (#329, decision 0131; Tracker2's services/bots/dynamic_stop.py).

A Dynamic stop rests on a side's short at the entry credit + the side's long - the buffer
(bots/stops.py), so the side stops when its spread is worth about the whole credit. It's
placed with the long at its fill; this pass, at most once a minute in the regular session,
prices the long at its live mid and moves the stop down when that's at least a tick lower.
It never moves a stop up: a long gaining value leaves it where it is. Only your switch to
a Dynamic stop on the run page may loosen one (bot_manage.set_closing_order).

Guards (Tracker2's spec, and its fixes 1a87ccec and 904365cc):
- only stops whose latest arithmetic is a Dynamic stop (bot_exits.is_dynamic): a stop any
  other rule moved since (a profit or time action, your change) belongs to that rule now;
- a resting stop that's working at the broker, or one the app holds on its short; never a
  side that's closing, closed, or held back (an order the app can't vouch for may be live);
- quotes from the stream through bot_exits._quote, never older than 30 s (there's no
  cached fallback of any age: a stale low long would tighten a stop the ratchet can't
  undo). Every long leg needs a usable mid (bid 0 or more, ask above 0 and not under the
  bid: 0/0 is no market, not a free option), the short a fresh ask above 0; else no move.
  A long group that's closed counts 0;
- never to a level the short trades through: at or below its ask, one tick above the ask
  (stops.next_trigger, on a valid tick across a tick tier);
- a resting stop moves by one replace through the order path (orders.replace: the central
  check, preview vs live, size limits, Stop all bots, Freeze), never cancel-then-place, so
  there's no moment without a stop. A refused or unclear replace leaves the row and the
  old order as they were (the monitor re-arms a stop that's gone) and alerts once a day.

Moves are quiet: the stop's log ("moved down as the long decayed") and its arithmetic, no
alert. The rows' updated_at is left alone: the watched stops' lapse check reads it."""

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import exit_math, exit_plan, stops, timing
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_now
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_exits, orders
from optionsjourney.trading.orders import plain

logger = logging.getLogger(__name__)

INTERVAL = timedelta(seconds=60)
_last: datetime | None = None
_said: set[tuple[int, date]] = set()  # (stop, New York day) whose refusal was alerted
MOVED = "moved down as the long decayed"


def reset() -> None:
    """For tests, and what a restart does: nothing in memory."""
    global _last
    _last = None
    _said.clear()


def _summary() -> dict:
    return {"checked": 0, "moved": 0, "skipped": 0, "errors": 0}


def ratchet_pass(deps) -> dict:
    """One pass over every Dynamic stop (the bot monitor, every 30 s): at most once a
    minute, in the regular session, not while Freeze is on. Never raises."""
    global _last
    summary = _summary()
    try:
        now = deps.now()
        if _last is not None and now - _last < INTERVAL:
            return summary
        _last = now
        if session_now("SPY", now.astimezone(MARKET_TZ)) != "regular" or bot_exits._frozen(deps):
            return summary
        for exit_id in _dynamic_rows(deps):
            try:
                _ratchet(deps, exit_id, summary)
            except Exception:  # noqa: BLE001 -- one stop never stops the others
                logger.exception("dynamic stop %s: the ratchet failed", exit_id)
                summary["errors"] += 1
        if summary["moved"] or summary["errors"]:
            logger.info("dynamic stop pass: %s", summary)
    except Exception:  # noqa: BLE001 -- the loop goes on
        logger.exception("the dynamic stop pass failed")
    return summary


def _dynamic_rows(deps) -> list[int]:
    """Working resting stops and stops the app holds on a short, whose latest arithmetic
    is a Dynamic stop."""
    with deps.session_factory() as session:
        rows = session.scalars(
            select(BotExit)
            .where(BotExit.kind == "stop", BotExit.state.in_(("working", bot_exits.WATCHING)))
            .order_by(BotExit.id)
        )
        return [
            r.id
            for r in rows
            if bot_exits.is_dynamic(r) and not r.watched
            and (r.broker_order_id if r.state == "working" else r.app_held)
        ]  # fmt: skip


@dataclass(frozen=True)
class _Seen:
    """A dynamic stop as read before its quotes (no lock held)."""

    exit_id: int
    run_id: int
    bot_id: int
    position: int
    state: str
    order_id: str | None
    log_len: int
    trigger: Decimal
    contracts: int
    anchor: exit_math.GroupLeg
    calc: dict


def _read(deps, exit_id: int) -> _Seen | None:
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        if row is None or row.state not in ("working", bot_exits.WATCHING):
            return None
        run = session.get(BotRun, row.run_id)
        return _Seen(
            row.id, row.run_id, run.bot_id, row.group_position, row.state, row.broker_order_id,
            len(row.log or []), row.trigger, row.contracts, bot_exits._anchor_leg(row),
            stops.calc_of(row.log) or {},
        )  # fmt: skip


def _side_rows(session, run_id: int, position: int) -> list[BotExit]:
    return list(session.scalars(select(BotExit).where(
        BotExit.run_id == run_id, BotExit.group_position == position)))  # fmt: skip


def _side_free(rows) -> bool:
    """The side is open and nothing it may have live is in doubt: no close running or
    claimed, nothing held back (bot_exits.held_back)."""
    if timing.side_state((r.kind, r.state) for r in rows) != "open":
        return False
    return bot_exits.held_back(rows) is None


def usable_mid(quote) -> Decimal | None:
    """A long leg's mid for the ratchet (Tracker2's dynamic_stop._mid): bid 0 or more, ask
    above 0 and not under the bid. Unlike exit_math.leg_mid, 0/0 is no price: priced at 0,
    it would drop the stop by the long's whole value, which the ratchet can't undo."""
    if quote is None or quote.bid is None or quote.ask is None:
        return None
    if quote.bid < 0 or quote.ask <= 0 or quote.ask < quote.bid:
        return None
    return (quote.bid + quote.ask) / 2


def _priced(deps, run_id: int, legs, group: int | None) -> Decimal | None:
    """The long now, per unit: 0 with no long legs, or a long group that's closed; else
    each leg's fresh, usable mid (usable_mid) x its ratio; None when one has none.
    `legs`: (symbol, ratio) pairs."""
    if not legs:
        return Decimal(0)
    if group is not None:
        with deps.session_factory() as session:
            rows = _side_rows(session, run_id, group)
            if timing.side_state((r.kind, r.state) for r in rows) == "closed":
                return Decimal(0)
    total = Decimal(0)
    for symbol, ratio in legs:
        mid = usable_mid(bot_exits._quote(deps, symbol))  # fresh: 30 s at most
        if mid is None:
            return None
        total += mid * ratio
    return total


def long_mid(deps, seen: _Seen) -> Decimal | None:
    """The side's long now, as its stop's arithmetic recorded it (its legs and group)."""
    legs = [(leg["symbol"], int(leg.get("ratio", 1))) for leg in seen.calc.get("long_legs") or []]
    return _priced(deps, seen.run_id, legs, seen.calc.get("long_group"))


def live_long(deps, run_id: int, run_legs, group) -> dict | None:
    """A switch to a Dynamic stop (bot_manage.set_closing_order; Tracker2's drawer reads the
    long's live mid once): the long's part of stops.stop_calc at its live mid, or None when
    an open long has no fresh, usable quote (the switch is refused: never a guess)."""
    bought, position = exit_plan.long_legs(run_legs, group)
    legs = tuple((leg.symbol, leg.ratio) for leg in bought)
    value = _priced(deps, run_id, legs, position)
    if value is None:
        return None
    return {"long_value": value, "long_legs": legs, "long_group": position,
            "note": None if bought else exit_plan.NO_LONG}  # fmt: skip


def _ratchet(deps, exit_id: int, summary: dict) -> None:
    seen = _read(deps, exit_id)
    if seen is None:
        return
    summary["checked"] += 1
    calc = seen.calc
    if calc.get("entry_credit") is None or seen.trigger is None:
        summary["skipped"] += 1
        return
    long_value = long_mid(deps, seen)
    short = seen.anchor.symbol
    quote = bot_exits._quote(deps, short)
    ask = quote.ask if quote is not None and quote.ask is not None and quote.ask > 0 else None
    if long_value is None or ask is None:
        # No fresh long, or no telling whether the new level is already through the
        # market: leave the stop where it is
        summary["skipped"] += 1
        return
    credit, buffer = Decimal(calc["entry_credit"]), Decimal(calc.get("co_amount") or 0)
    raw = stops.dynamic_trigger(credit, long_value, buffer) / seen.anchor.ratio
    tick, computed = exit_math.round_stop(short, raw)
    fill = Decimal(calc["short_fill"]) if calc.get("short_fill") is not None else Decimal(0)
    target = stops.next_trigger(seen.trigger, computed, ask, fill, tick, short)
    if target is None:
        summary["skipped"] += 1
        return
    moved = stops.dynamic_moved(calc, long_value, raw, tick, target)
    moved["moved_by"] = "the Dynamic stop as the long decayed"  # the run page says it first
    if target != computed:
        moved["note"] = f"held one tick above the short's ask {ask}"
    entry = {"event": f"{MOVED}: {seen.trigger} → {target}", "long_mid": f"{long_value:f}",
             "stop_calc": moved}  # fmt: skip
    if seen.state == bot_exits.WATCHING:
        _move_held(deps, seen, target, entry, summary)
    else:
        _move_resting(deps, seen, target, entry, summary)


def _unchanged(row, seen: _Seen) -> bool:
    return (
        row is not None and row.state == seen.state and row.broker_order_id == seen.order_id
        and len(row.log or []) == seen.log_len and row.trigger == seen.trigger
    )  # fmt: skip


def _write(deps, row: BotExit, entry: dict, **changes) -> None:
    """The move on the row, its updated_at left alone (the watched stops' lapse check
    reads it as when the stop last needed watching from)."""
    for name, value in changes.items():
        setattr(row, name, value)
    row.log = [*row.log, {"at": deps.now().isoformat(), **entry}]


def _move_held(deps, seen: _Seen, target: Decimal, entry: dict, summary: dict) -> None:
    """A stop the app holds on its short: its new level written, nothing sent."""
    with bot_exits.EXITS_LOCK, deps.session_factory() as session:
        row = session.get(BotExit, seen.exit_id)
        if not _unchanged(row, seen) or not _side_free(_side_rows(session, seen.run_id,
                                                                  seen.position)):  # fmt: skip
            summary["skipped"] += 1
            return
        _write(deps, row, entry, trigger=target)
        session.commit()
    summary["moved"] += 1
    logger.info("run %s group %s: dynamic stop %s -> %s (watched by the app)",
                seen.run_id, seen.position, seen.trigger, target)  # fmt: skip


def _move_resting(deps, seen: _Seen, target: Decimal, entry: dict, summary: dict) -> None:
    """A resting stop: one replace through the order path, sized as rearm sizes it (its
    units x the short's ratio, never past what the broker shows held short)."""
    run, bot = bot_exits._run_and_bot(deps, seen.run_id)
    try:
        held = max(-bot_exits._positions(deps, run).get(plain(seen.anchor.symbol), 0), 0)
    except Exception:  # noqa: BLE001 -- no positions: no move this pass
        logger.exception("dynamic stop %s: couldn't read positions", seen.exit_id)
        summary["skipped"] += 1
        return
    size = min(seen.contracts * seen.anchor.ratio, held)
    if size <= 0:
        summary["skipped"] += 1  # the monitor reads the stop and settles the side
        return
    request = bot_exits._stop_request(bot, run, replace(seen.anchor, ratio=1), size, target)
    with bot_exits.EXITS_LOCK:
        with deps.session_factory() as session:
            row = session.get(BotExit, seen.exit_id)
            if not _unchanged(row, seen) or not _side_free(
                _side_rows(session, seen.run_id, seen.position)
            ):
                summary["skipped"] += 1
                return
        result = orders.replace(deps.session_factory, deps.clients(), request, seen.order_id,
                                now=deps.now(), notify=deps.notify)  # fmt: skip
        if bot_exits._placed(result):
            with deps.session_factory() as session:
                row = session.get(BotExit, seen.exit_id)
                _write(deps, row, entry, trigger=target,
                       broker_order_id=result.broker_order_id or seen.order_id)  # fmt: skip
                session.commit()
            summary["moved"] += 1
            logger.info("run %s group %s: dynamic stop %s -> %s, order %s -> %s", seen.run_id,
                        seen.position, seen.trigger, target, seen.order_id,
                        result.broker_order_id)  # fmt: skip
            return
    if result.action == "preview":  # a run in Preview: nothing rests to move
        summary["skipped"] += 1
        return
    summary["errors"] += 1
    logger.warning("run %s group %s: dynamic stop %s -> %s not moved: %s", seen.run_id,
                   seen.position, seen.trigger, target, result.message)  # fmt: skip
    _say_refused(deps, seen, bot, target, result.message)


def _say_refused(deps, seen: _Seen, bot, target: Decimal, message: str) -> None:
    """Once a day per stop: the move was refused or unclear. A refusal leaves the old stop
    working; an unclear one may have moved it (the monitor follows a replaced order)."""
    day = deps.now().astimezone(MARKET_TZ).date()
    if (seen.exit_id, day) in _said:
        return
    _said.add((seen.exit_id, day))
    unclear = message.startswith(orders.UNCLEAR)
    stands = (
        "Your broker may have moved it: check your broker." if unclear
        else f"The stop at {seen.trigger} still stands."
    )  # fmt: skip
    bot_exits._alert(
        deps, f"{bot.name}: Dynamic stop not moved",
        f"{bot_exits._group_name(bot, seen.position)}: its Dynamic stop wasn't moved down to "
        f"{target} ({message}). {stands} The app tries again each minute.",
        bot.id, run_id=seen.run_id,
    )  # fmt: skip
