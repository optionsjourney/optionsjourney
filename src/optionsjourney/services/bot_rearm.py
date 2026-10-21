"""Restore a bot trade's missing exits, on demand, with a dry run (spec: bots slice 18,
section 1; Tracker2's services/bots/rearm.py: `rearm_trade`, `rearm_all`, and
config_bp.rearm_protections).

A stop or target can still go missing: a stop refused or past its 3 re-arms a day, one
the broker cancelled, a target a close couldn't put back, a futures target that ended at
TastyTrade. bots/rearm.py says what each open group should have and what's missing;
this places only that, through the paths every exit takes:

- a stop: checked against the short's fresh ask (or a watched group's live P&L) first,
  then written "pending" at its last trigger and placed by bot_exits.rearm(first=True),
  which reads the broker's positions, sizes it to what's held short and never sends a
  stop with nothing held (not counted as a re-arm);
- a target the app arms: armed again at its last close-at, sized to what's open, once the
  broker shows the group held;
- a futures target: placed again at TastyTrade (bot_exits._send_target) once the broker
  shows the legs.

Nothing present is touched and nothing is cancelled. Restore runs under EXITS_LOCK and
reads everything again there (rearm._still_missing). Every item that isn't placed is
said, and alerted once for the trade. Never raises."""

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import caps, exit_math, integrity, rearm, stops, watched_stops
from optionsjourney.bots.definition import GroupDef
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_entry, bot_exits, bot_manage, bot_store
from optionsjourney.services.bot_exits import EXITS_LOCK
from optionsjourney.trading.orders import plain

logger = logging.getLogger(__name__)
RESTORED = "restored by you"


@dataclass
class Item:
    """One line of the dry run or of what Restore did (Tracker2's RearmItem)."""

    run_id: int
    bot_name: str
    group: str
    position: int
    kind: str  # "stop", "target" or "group"
    contracts: int | None
    price: Decimal | None
    status: str  # rearm.MISSING / BY_HAND / HELD / BUSY, then PLACED / REFUSED / ERROR
    detail: str = ""

    @property
    def done(self) -> bool:
        return self.status == rearm.PLACED

    @property
    def what(self) -> str:
        return {"stop": "stop", "target": "profit target"}.get(self.kind, "exits")


def _plan(session, run: BotRun, bot, now) -> list[tuple[int, rearm.Missing, int]]:
    """[(group position, what's missing, open units)] for one run, read now."""
    out = []
    groups = {g.position: g for g in bot.groups} if bot is not None else {}
    for position in sorted({leg["group"] for leg in run.legs}):
        group = groups.get(position)
        if group is None:
            continue
        side = bot_manage._read(session, run.id, position)
        if side is None:
            continue
        want = rearm.Want(
            stop=group.stop_value is not None and not run.pt_only,
            target=group.target_value is not None,
            stop_off=side.stop_off,
            target_off=side.target_off,
            held_back=bot_exits.held_back(side.rows) is not None,
            target_price=_target_price(group, side),
        )
        rows = [rearm.Row(r.id, r.kind, r.state, r.trigger, r.limit) for r in side.rows]
        by_id = {r.id: r for r in side.rows}
        for item in rearm.missing(rows, want):
            stop = by_id.get(item.row_id) if item.kind == "stop" else None
            if stop is not None and item.status == rearm.MISSING:
                item = _stop_check(session, run, stop, item, now)
            out.append((position, item, side.size))
    return out


def _stop_check(session, run: BotRun, stop, item: rearm.Missing, now) -> rearm.Missing:
    """A missing stop Restore may place, or why not: one not known gone at the broker (a
    second stop could double it), or a watched stop that already closed the group 3
    times today (its limit, decision 0037: left to you)."""
    if not _dead(stop):
        return rearm.Missing("stop", rearm.HELD, stop.id, item.price, UNSURE)
    if (stop.watched or bot_exits.holds_short(stop)) and _watched_closes_today(
        session, run.id, stop.group_position, now
    ) >= watched_stops.FIRES_PER_DAY:
        return rearm.Missing("stop", rearm.BY_HAND, stop.id, item.price, WATCH_LIMIT)
    return item


WATCH_LIMIT = (
    "the app watching it has already closed this group 3 times today (a watched stop's "
    "limit), so it's left to you; close the group or set a stop from the run page"
)


def _watched_closes_today(session, run_id: int, position: int, now) -> int:
    """How many closes this group's watched stop started today
    (bot_watched_stops._closes_today)."""
    from types import SimpleNamespace

    from optionsjourney.services import bot_watched_stops

    return bot_watched_stops._closes_today(
        session, SimpleNamespace(run_id=run_id, position=position), now
    )


UNSURE = (
    "its last stop isn't known to be gone at your broker; check your broker, then set the "
    "stop from the run page"
)


def _dead(stop) -> bool:
    """A stop row that's safe to replace: it failed, or it's known gone at the broker
    (bot_manage._replaceable: the broker showed it cancelled, or the close that pulled it
    is over there). A stop that "failed" on an Unclear send (rearm and place_protections
    write "failed" for any refusal) may be live at the broker: not known gone."""
    if bot_exits.unclear_send(stop):
        return False
    return bot_manage._replaceable(stop)


def _target_price(group: GroupDef, side) -> Decimal | None:
    """A target's close-at from the bot's group on the group's entry, as place_protections
    prices it (stops.target_limit), for a group with no target row to take it from."""
    entry = side.group.entry
    if entry is None or group.target_value is None:
        return None
    limit, problem = stops.target_limit(group, entry)
    return None if problem is not None else limit


def _name(bot, position: int) -> str:
    return bot_exits._group_name(bot, position)


def _items(run: BotRun, bot, plan) -> list[Item]:
    return [
        Item(run.id, bot.name if bot else "A bot", _name(bot, position), position, m.kind,
             size if m.kind != "group" else None, m.price, m.status, m.detail)
        for position, m, size in plan
    ]  # fmt: skip


def open_runs(session_factory, now) -> list[int]:
    """Every held bot run still live (some leg not past its expiry), oldest first."""
    with session_factory() as session:
        found = session.scalars(
            select(BotRun)
            .where(BotRun.status.in_(caps.HOLDING), BotRun.paper.is_(False))
            .order_by(BotRun.id)
        )
        return [
            r.id
            for r in found
            if r.filled_legs and integrity.is_live([leg["symbol"] for leg in r.legs], now)
        ]


def rearm_trade(deps, run_id: int, *, dry_run: bool) -> list[Item]:
    """What's missing on one trade (dry_run), or what Restore did about it. Never
    raises: a breakage comes back as an "error" item and is alerted."""
    try:
        run, bot = _load(deps, run_id)
        if run is None:
            return []
        if dry_run:
            with deps.session_factory() as session:
                return _items(
                    run, bot, _plan(session, session.get(BotRun, run_id), bot, deps.now())
                )
        with EXITS_LOCK:
            items = _apply(deps, run_id, bot)
        _alert_unrestored(deps, bot, run_id, items)
        return items
    except Exception as e:  # noqa: BLE001 -- the page always answers
        logger.exception("restoring run %s's exits failed", run_id)
        why = f"something went wrong part-way ({e.__class__.__name__}); check your broker"
        item = Item(run_id, "", "", 0, "group", None, None, rearm.ERROR, why)
        if not dry_run:
            bot_entry.alert(
                deps, "Missing exits not restored",
                f"Restoring run {run_id}'s missing exits went wrong part-way. Check its run "
                "page and your broker.", f"/automation/runs/{run_id}", run_id=run_id,
            )  # fmt: skip
        return [item]


def rearm_all(deps, *, dry_run: bool) -> list[Item]:
    """rearm_trade over every open bot trade. Never raises."""
    items = []
    for run_id in open_runs(deps.session_factory, deps.now()):
        items += rearm_trade(deps, run_id, dry_run=dry_run)
    return items


def _load(deps, run_id: int):
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or run.status not in caps.HOLDING or not run.filled_legs:
            return None, None
        session.expunge(run)
    return run, bot_store.load(deps.session_factory, run.bot_id)


def _apply(deps, run_id: int, bot) -> list[Item]:
    """Restore, under the lock: everything read again, then each missing item placed,
    stop first. Refused for the whole trade in preview or under Freeze."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        items = _items(run, bot, _plan(session, run, bot, deps.now()))
        live, futures = bool(run.live), bool(run.pt_only)
        resting = {
            item.position for item in items
            if item.kind == "target" and bot_exits.target_rested(session, run_id, item.position)
        }  # fmt: skip
    pending = [i for i in items if i.status == rearm.MISSING]
    for item in items:
        if item.status in (rearm.BY_HAND, rearm.HELD, rearm.BUSY):
            item.detail = f"not restored: {item.detail}"
            item.status = rearm.REFUSED
    if not pending:
        return items
    why = None
    if not live:
        why = "this trade ran in preview, so there's nothing at the broker to protect"
    elif bot_exits._frozen(deps):
        why = "orders are frozen (Freeze is on); try again once it's off"
    if why:
        for item in pending:
            item.status, item.detail = rearm.REFUSED, why
        return items
    for item in pending:
        try:
            if item.kind == "stop":
                _restore_stop(deps, run_id, item)
            elif futures or item.position in resting:
                _restore_resting_target(deps, run_id, item)
            else:
                _restore_target(deps, run_id, item, bot)
        except Exception as e:  # noqa: BLE001 -- one item never stops the others
            logger.exception("restoring run %s's %s failed", run_id, item.kind)
            item.status = rearm.ERROR
            item.detail = f"something went wrong part-way ({e.__class__.__name__})"
    return items


def _side(deps, run_id: int, position: int):
    with deps.session_factory() as session:
        return bot_manage._read(session, run_id, position)


def _still(side, item: Item) -> rearm.Missing | None:
    """The item as a fresh read sees it, or None if it's there by now (_still_missing)."""
    if side is None:
        return None
    present = rearm.STOP_PRESENT if item.kind == "stop" else rearm.TARGET_PRESENT
    if any(r.kind == item.kind and r.state in present for r in side.rows):
        return None
    if side.state != "open" or bot_exits.held_back(side.rows) is not None:
        return None
    if (item.kind == "stop" and side.stop_off) or (item.kind == "target" and side.target_off):
        return None
    return rearm.Missing(item.kind, rearm.MISSING, None, item.price)


def _latest(side, kind: str):
    found = [r for r in side.rows if r.kind == kind]
    return max(found, key=lambda r: r.id) if found else None


def _restore_stop(deps, run_id: int, item: Item) -> None:
    side = _side(deps, run_id, item.position)
    if _still(side, item) is None:
        item.status, item.detail = rearm.REFUSED, "it's there now (nothing sent)"
        return
    latest = _latest(side, "stop")
    if latest is None or latest.trigger is None:
        item.status, item.detail = rearm.REFUSED, rearm.SET_BY_HAND
        return
    if not _dead(latest):
        item.status, item.detail = rearm.REFUSED, f"not restored: {UNSURE}"
        return
    trigger = latest.trigger
    ask, pnl = bot_manage._live_check(deps, side)
    if side.anchor is None:
        problem = bot_exits.fires_at_once(trigger, pnl)
    elif ask is None:
        problem = f"no fresh quote for the short to show a stop at {trigger} won't fire at once"
    elif trigger <= ask:
        problem = (
            f"a stop at {trigger} is at or below the short's ask {ask}, so it would fire at once"
        )
    else:
        problem = None
    if problem is not None:
        item.status, item.detail = rearm.REFUSED, f"not placed: {problem}"
        return
    # The row stays "failed" while it's placed: rearm(first=True) reads the broker's
    # positions, sizes to what's held short and sends it (not a re-arm). A refusal leaves
    # it "failed", never "pending": the monitor's pending placement has no live check, so a
    # refused Restore must not be placed later on its own.
    with deps.session_factory() as session:
        row = session.get(BotExit, latest.id)
        row.contracts = min(row.contracts, side.size)  # never more than is left open
        row.state, row.broker_order_id = "failed", None
        bot_exits._log(row, deps, event=RESTORED)
        session.commit()
    placed = bot_exits.rearm(deps, latest.id, first=True)
    with deps.session_factory() as session:
        after = session.get(BotExit, latest.id)
        if after.state == "pending":  # Freeze came on meanwhile: not placed, and not later
            after.state = "failed"
            bot_exits._log(after, deps, event="not restored: orders are frozen")
            session.commit()
        state, size, last = after.state, after.contracts, (after.log or [{}])[-1]
    item.contracts = size
    if placed and state in rearm.STOP_PRESENT:
        item.status = rearm.PLACED
        item.detail = "watched by the app" if state == bot_exits.WATCHING else f"at {trigger}"
    elif state == "expired":
        item.status = rearm.REFUSED
        item.detail = "the broker shows nothing held: nothing to protect"
    else:
        said = last.get("result") or last.get("event") or "the order path refused it"
        item.status, item.detail = (
            rearm.REFUSED,
            f"not placed ({said}); nothing will place it later",
        )


def _held(deps, run_id: int, side) -> int | None:
    """The whole units the broker shows held of the group, capped at its open units;
    None when positions can't be read."""
    run, _ = bot_exits._run_and_bot(deps, run_id)
    try:
        positions = bot_exits._positions(deps, run)
    except Exception:  # noqa: BLE001 -- never arm on a guess
        logger.exception("couldn't read positions to restore run %s's target", run_id)
        return None
    return min(bot_manage._held_units(positions, side.legs), side.size)


def _restore_target(deps, run_id: int, item: Item, bot) -> None:
    """An app-armed target: armed again (or, with no row to re-use, a new one), sized to
    what's held and open. Nothing is sent: the watch pass fires it."""
    side = _side(deps, run_id, item.position)
    if _still(side, item) is None:
        item.status, item.detail = rearm.REFUSED, "it's there now (nothing changed)"
        return
    held = _held(deps, run_id, side)
    if held is None:
        item.status, item.detail = rearm.ERROR, "couldn't read your positions; nothing changed"
        return
    if held < 1:
        item.status, item.detail = (
            rearm.REFUSED,
            "the broker shows nothing held: nothing to protect",
        )
        return
    latest = _latest(side, "target")
    with deps.session_factory() as session:
        if latest is not None and _reusable(latest):
            row = session.get(BotExit, latest.id)
            row.state, row.limit = "armed", item.price
            row.contracts = min(row.contracts, held)
            bot_exits._breaches.pop(row.id, None)
            bot_exits._log(row, deps, event=f"armed again: {RESTORED}")
        else:
            row = _new_target(session, deps, run_id, item, side, held, "armed")
        session.commit()
        item.contracts = row.contracts
    item.status, item.detail = rearm.PLACED, f"armed at {item.price}"


def _reusable(row) -> bool:
    """A target row Restore may write over: it ended without closing anything (cancelled
    or failed, no fill). Any other (one that closed part of the group, say) keeps its
    record, and the restored target gets a row of its own."""
    return row.state in ("cancelled", "failed") and row.fill_price is None


def _new_target(session, deps, run_id: int, item: Item, side, units: int, state: str,
                rests: bool = False):  # fmt: skip
    """A restored target's own row. `rests`: it was placed at TastyTrade (decision 0048),
    which its log keeps ({"rests": "target"}), so once it ends Restore places it again,
    never arms it (bot_exits.rested)."""
    run_row = session.get(BotRun, run_id)
    now = deps.now()
    first = {"at": now.isoformat(), "event": f"{'placed' if rests else 'armed'}: {RESTORED}",
             "close_at": f"{item.price:f}"}  # fmt: skip
    row = BotExit(
        run_id=run_id, group_position=item.position, kind="target", state=state,
        **bot_exits.group_columns(session, run_row, item.position, side.rows),
        contracts=units, limit=item.price, rearms=0,
        log=[first, {"rests": "target"}] if rests else [first],
        created_at=now, updated_at=now,
    )  # fmt: skip
    session.add(row)
    session.flush()
    return row


def _restore_resting_target(deps, run_id: int, item: Item) -> None:
    """A target that rests at TastyTrade (a futures run's, or a group that chose it, decision
    0048): placed again at its last limit, once the broker shows the group's legs
    (bot_exits._place_pending_target's steps). Its dead row is re-used; it's the only
    closing order on its legs (the stop is the app's)."""
    side = _side(deps, run_id, item.position)
    if _still(side, item) is None:
        item.status, item.detail = rearm.REFUSED, "it's there now (nothing sent)"
        return
    latest = _latest(side, "target")
    if latest is None:
        where = bot_exits.run_broker_name(deps, run_id)
        item.status, item.detail = rearm.REFUSED, f"no target to put back; place it at {where}"
        return
    run, bot = bot_exits._run_and_bot(deps, run_id)
    legs = exit_math.row_legs(latest)
    units = min(latest.contracts, side.size)
    # Bots slice 21: never a second closing order over one already working on its legs
    # (an order the app doesn't know, after a change cut off at the broker)
    try:
        working = bot_exits._broker(deps, run).working_orders(run.account)
    except Exception:  # noqa: BLE001 -- can't tell: nothing placed
        logger.exception("couldn't read run %s's working orders before a restore", run_id)
        item.status, item.detail = rearm.REFUSED, "your working orders couldn't be read"
        return
    mine = {plain(leg.symbol) for leg in legs}
    busy = [o.broker_order_id for o in working
            if any(plain(leg.symbol) in mine for leg in o.legs)]  # fmt: skip
    if busy:
        item.status, item.detail = (
            rearm.REFUSED,
            (f"an order on its legs is working at your broker ({', '.join(busy)}); check it"),
        )
        return
    shown = bot_exits._legs_show(deps, run, {leg.symbol: units * leg.ratio for leg in legs})
    state, order_id, rested, message = bot_exits._send_target(
        deps, bot, run, legs, units, item.price, shown
    )
    unclear = bot_exits.sent_unclear(state, message)  # it may rest at TastyTrade
    with deps.session_factory() as session:
        if _reusable(latest):
            row = session.get(BotExit, latest.id)
        elif state == "failed" and not unclear:
            row = None  # nothing rests: the old row's record stays as it was
        else:  # a row of its own (an unclear one too: it's held on it)
            row = _new_target(session, deps, run_id, item, side, units, state, rests=True)
        if row is None:
            pass
        elif state == "failed":
            bot_exits._log(row, deps, event=f"not restored: {message}")
        else:
            row.state, row.broker_order_id, row.contracts = state, order_id, units
            if rested is not None:
                row.limit = rested
            bot_exits._log(row, deps, event=RESTORED, result=message)
        session.commit()
        held_id = row.id if unclear else None
    if held_id is not None:  # final review C1: held, never a second closing order
        message = bot_exits._hold_unclear_send(deps, bot, run_id, item.position, held_id,
                                               "restoring its profit target")  # fmt: skip
    item.contracts = units
    if state == "working":
        item.status, item.detail = rearm.PLACED, f"{bot_exits.rests_words(run)} (order {order_id})"
    elif state == "pending":
        item.status, item.detail = rearm.PLACED, "waiting for Freeze to go off"
    else:
        item.status, item.detail = rearm.REFUSED, message


def _alert_unrestored(deps, bot, run_id: int, items: list[Item]) -> None:
    """One alert for the trade when anything missing is still missing."""
    left = [i for i in items if i.kind in ("stop", "target", "group") and not i.done]
    if not left or bot is None:
        return
    words = "; ".join(f"{i.group}'s {i.what}: {i.detail}" for i in left)
    bot_exits._alert(
        deps, f"{bot.name}: missing exits not restored",
        f"Restore couldn't put back everything on run {run_id}: {words}. That group is still "
        "missing an exit. Check your broker.", bot.id, run_id=run_id,
    )  # fmt: skip
