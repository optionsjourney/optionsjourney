"""A bot trade's exits (spec: the first automation, section 4; Tracker2's
exits.py, exit_monitor.py, remainder.py, rearm.py, watch_loop.py).

Per group: a resting stop-market on its one short (multi-leg stops can't rest);
when it fills, each of the group's other legs is handled as the group says, on
its own order: sold now (market with a fresh bid, else a one-tick limit), at a
limit, chased, or left open. A
profit target is armed, not resting (TastyTrade refuses a second closing
order on a contract): the watch loop fires it on two fresh passes,
cancelling the stop first. A stop
that disappears is re-placed, up to 3 times a side a day. Every order goes
through the order path; while Freeze is on, nothing is sent, and a stop
refused for it waits as "pending" until Freeze is off.

A run's exits follow the run: its account and its live/preview setting from
when it fired, not the bot's current ones. Only a real send, replace or cancel
counts as placed; a preview or a refusal placed nothing."""

import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update

from optionsjourney.bots import (
    exit_math,
    exit_plan,
    futures,
    overrides,
    stops,
    timing,
    underlyings,
    watch,
    watched_stops,
)
from optionsjourney.bots.definition import GroupDef
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_now
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import Bot, BotExit, BotRun, BotTradeOverride
from optionsjourney.services import bot_entry, bot_store, orders
from optionsjourney.services.bot_chase import chase, read_after_cancel, record_order
from optionsjourney.trading import ticks
from optionsjourney.trading.orders import (
    BrokerOrder,
    Leg,
    Order,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
    filled_units,
    plain,
)

logger = logging.getLogger(__name__)
POSITION_TRIES = 5  # exits.place_group_protections: 5 reads, 1 s apart
# monitor_pass's steps, fire_target's claim, and every re-arm. Re-entrant: a close's job
# re-arms under it, and in tests the job may run inside the pass that started it. Never held
# across the monitor's read of a working exit's order (_check): that read is re-checked
# under the lock before anything is written, so a slow broker never holds up the other's.
EXITS_LOCK = threading.RLock()
_breaches: dict[int, int] = {}  # target id -> consecutive passes at or past its threshold
# Exits a live job is closing (a target "firing", a wing "chasing"); recovery leaves them alone.
FIRING: set[int] = set()
FROZEN = "Orders are frozen"  # how the order path's Freeze refusal starts
CANCELLED_FOR_TARGET = "cancelled for the profit target"
CANCELLED_FOR_TIMED = "cancelled for the timed close"  # slice 4: the stop and target it pulled
MOVING_STOP = "cancelling to move it (time action)"  # a stop mid-move: recovery's marker
# Slice 6, managing a trade from the journal (services/bot_manage.py):
MOVING_STOP_BY_YOU = "cancelling to move it (you)"  # the same marker for a change you made
MOVING_STOP_PROFIT = "cancelling to move it (profit action)"  # slice 7: a profit action's move
MOVING = (MOVING_STOP, MOVING_STOP_BY_YOU, MOVING_STOP_PROFIT)
EXITS_OFF = "exits turned off for this trade"  # a stop or target you turned off
TURNING_OFF = "cancelling to turn exits off"  # a stop mid-way to off: recovery's marker
CANCELLED_FOR_CLOSE = "cancelled for a close you asked for"
CLOSES = ("target", "timed", "close")  # the kinds whose close runs as a job, "firing" meanwhile
_WHAT = {"target": "profit target", "timed": "timed close", "close": "close"}
_PULLED_BY = {"timed": CANCELLED_FOR_TIMED, "close": CANCELLED_FOR_CLOSE}  # what a claim marks


# A stop move onto a stop that isn't at the broker (pending): the side isn't protected yet
NOT_AT_BROKER = "but the side has no stop at your broker until it's placed"

# Bots slice 11 (decision 0037): a stop the app watches instead of resting it at the broker.
# Its stop rows are "watching": the trigger and size are the app's record, and nothing is
# at the broker. Decision 0060: a Schwab run's exits follow TastyTrade's rules; a Schwab
# stop opened before then was marked app_held (migration 0036), so it stays watched.
WATCHING = "watching"
WATCHED = "watched by the app"
# Bots slice 13 (decision 0039): a group with no single short to rest on, or any group
# of a debit bot, has its stop watched by the app on the group's P&L, on every broker.
WATCHED_GROUP = f"{WATCHED} on the group's P&L: nothing rests at the broker"
# The rule a watched stop's close is started by ("close for the stop"). It always ends
# with the natural price and the leg-out, whatever the bot's exit chase says: a watched
# stop must actually close (the controller's ruling on review I-3).
WATCHED_STOP_BY = "stop"
# A side's current stop: at the broker ("working"), waiting to be placed ("pending"), or
# watched by the app ("watching").
STOP_LIVE = ("working", "pending", WATCHING)


def broker_name(run) -> str:
    """The run's broker as it's said: "TastyTrade" or "Schwab"."""
    return orders.BROKER_NAMES.get(run.broker, run.broker)


def watched_short(run) -> str:
    """Decision 0048: a group whose profit target rests has its stop held by the app on
    its short (watched on the short's quote): row.app_held."""
    return f"{WATCHED} on its short: its profit target rests at {broker_name(run)}"


WATCHED_SHORT = f"{WATCHED} on its short: its profit target rests at TastyTrade"


def holds_short(row) -> bool:
    """Whether the app, not the broker, holds this stop on its short: one whose group rests
    its target instead (decision 0048), or a Schwab stop opened before decision 0060
    (migration 0036). Read from the row, never the broker, so a stop the app holds is
    never sent to the broker as well."""
    return bool(row.app_held)


def _alert(deps, title, detail, bot_id, *, run_id=None, routine=False):
    bot_entry.alert(deps, title, detail, f"/automation/{bot_id}/runs", run_id=run_id,
                    routine=routine, bot_id=bot_id)  # fmt: skip


def _client(deps, broker: str):
    """The connected client for `broker`. Raises LookupError when it isn't connected:
    every caller treats that as an unreadable broker."""
    found = next((c for c in deps.clients() if getattr(c, "broker", "") == broker), None)
    if found is None:
        raise LookupError(f"{broker} isn't connected")
    return found


def _broker(deps, run: _Run):
    """The client of the broker the run fired at: its exits are read and traded there."""
    return _client(deps, run.broker)


def _frozen(deps) -> bool:
    return orders.switches(deps.session_factory).freeze


def _placed(result) -> bool:
    """True only if the order path really sent or replaced it (not a preview or refusal)."""
    return result.ok and result.action in ("send", "replace")


def _held_for_freeze(result) -> bool:
    return result.action == "refuse" and result.message.startswith(FROZEN)


# A stop left cancelled with one of these as its last event may have a live order
# behind it: a close that pulled it (and ended unclear, or was cut off), a stop move or
# a "turn exits off" cut off part-way (bots slice 6). Its recovery is the app's.
CLOSE_MARKERS = (CANCELLED_FOR_TARGET, CANCELLED_FOR_TIMED, CANCELLED_FOR_CLOSE)
LIVE_MARKERS = (*CLOSE_MARKERS, *MOVING, TURNING_OFF)


def live_evidence(log) -> bool:
    """A close's log says its order may still be live: it ended unclear, or recovery
    found its order live or a market buy-back it couldn't settle."""
    return any(
        str(e.get("event", "")).startswith("unclear")
        or e.get("close_order") == "live"
        or "market_order" in e
        for e in log or ()
    )


def _latest(rows, *kinds):
    found = [r for r in rows if r.kind in kinds]
    return max(found, key=lambda r: r.id) if found else None


def held_back(rows) -> str | None:
    """Why nothing new may be sent for this side (a second stop, or a second close,
    could over-close), from its exit rows: "close" (an earlier close may still be
    live: settle_held's to lift), "move" (a stop move or "turn exits off" cut off part-
    way: its own recovery's), or None."""
    stop = _latest(rows, "stop")
    last = stop.log[-1].get("event") if stop is not None and stop.log else None
    close = _latest(rows, *CLOSES)
    if close is not None and any(e.get("event") == SETTLED for e in close.log):
        close = None  # its close is over at the broker (settle_side): no longer held
        if last in CLOSE_MARKERS:
            last = None
    if stop is not None and stop.state == "cancelled" and last in LIVE_MARKERS:
        return "close" if last in CLOSE_MARKERS else "move"
    newer = close is not None and (stop is None or close.updated_at >= stop.updated_at)
    if newer and close.state == "failed" and live_evidence(close.log):
        return "close"
    if target_may_rest(rows):
        return "close"  # a second closing order could over-close: settle_side lifts it
    return None


def _unclear_target(rows) -> BotExit | None:
    """The group's latest target, held as unclear (TARGET_UNCLEAR: a target the app can't
    vouch for may rest at the broker) and not yet settled (settle_side), or None."""
    target = _latest(rows, "target")
    if target is None or target.state != "failed" or not live_evidence(target.log):
        return None
    if any(e.get("event") == SETTLED for e in target.log):
        return None
    return target


def target_may_rest(rows) -> bool:
    """Decision 0048: whether a profit target the app can't vouch for may still rest at the
    broker for this group. While it may, no close is sent on it (held_back), whichever row
    is newest: a close sent on top of it would be a second closing order."""
    return _unclear_target(rows) is not None


def _switch(session, run_id: int, position: int, column) -> bool:
    return bool(
        session.scalar(
            select(column).where(
                BotTradeOverride.run_id == run_id,
                BotTradeOverride.group_position == position,
            )
        )
    )


def stop_off(session, run_id: int, position: int) -> bool:
    """Whether you switched this side's stop off (bots slice 18; Tracker2's
    stop_loss_off). Every path that places, re-arms or fires a side's stop asks this
    first: nothing may place or fire its stop while it's on."""
    return _switch(session, run_id, position, BotTradeOverride.stop_off)


def target_off(session, run_id: int, position: int) -> bool:
    """Whether you switched this side's target off (Tracker2's profit_target_off):
    nothing may arm or fire its target while it's on."""
    return _switch(session, run_id, position, BotTradeOverride.target_off)


def exits_off(session, run_id: int, position: int) -> bool:
    """Both switched off: slice 6's "exits off". Since the author's ruling on bots slice 18
    question 4 nothing closing a side skips it for this (Tracker2: the switches only take
    the stop and target away): the timed close, a time action's close, watch rules and
    profit actions all still run."""
    return _switch(session, run_id, position, BotTradeOverride.exits_off)


STOP_BACK_ON = "the stop is back on"
# Bots slice 21: a trade's own stop style (Tracker2's per-trade stop_loss_unit/usd,
# stop_loss_co_mode/amount). A later move by its own rule clears it: the latest change wins.
STOP_STYLE = ("stop_unit", "stop_usd", "co_mode", "co_amount")


def clear_stop_style(deps, run_id: int, position: int) -> None:
    """The trade's own stop level and style forgotten once something else moved its stop
    (a profit action), so its chip never says what the stop no longer is."""
    with deps.session_factory() as session:
        row = session.scalar(_override_of(run_id, position))
        if row is None or all(getattr(row, n) is None for n in ("stop_pct", *STOP_STYLE)):
            return
        row.stop_pct = None
        for name in STOP_STYLE:
            setattr(row, name, None)
        session.commit()


def _override_of(run_id: int, position: int):
    return select(BotTradeOverride).where(
        BotTradeOverride.run_id == run_id, BotTradeOverride.group_position == position
    )


def trade_group_of(row: BotTradeOverride | None) -> overrides.TradeGroup | None:
    """A group's own settings on one trade as bots/overrides.py reads them (bots slice 21),
    or None without a row."""
    if row is None:
        return None
    actions = row.profit_actions
    return overrides.TradeGroup(
        stop_pct=row.stop_pct, target_pct=row.target_pct, stop_unit=row.stop_unit,
        stop_usd=row.stop_usd, co_mode=row.co_mode, co_amount=row.co_amount,
        on_stop_remainder=row.on_stop_remainder, remainder_limit=row.remainder_limit,
        use_profit_actions=row.use_profit_actions,
        profit_actions=None if actions is None else tuple(
            bot_store.profit_of(raw) for raw in actions if isinstance(raw, dict)
        ),
    )  # fmt: skip


def trade_group(session, run_id: int, position: int) -> overrides.TradeGroup | None:
    """This trade's own settings for the group, or None (the group's own)."""
    return trade_group_of(session.scalar(_override_of(run_id, position)))


def effective_group(session, run_id: int, bot, position: int) -> GroupDef | None:
    """The bot's group as this trade runs it (overrides.effective_group), or None when the
    bot has no group there."""
    group = next((g for g in bot.groups if g.position == position), None) if bot else None
    if group is None:
        return None
    return overrides.effective_group(group, trade_group(session, run_id, position))


def revive_stop(deps, run_id: int, position: int, why: str) -> int | None:
    """A profit action's stop move on a side whose stop you switched off turns the stop
    back on (bots slice 18; Tracker2's management.record_profit_action_fill: "the action
    sets a stop, even over a removed one", reached from scale_out for a group's action).
    The side's latest stop row, known dead (switched off, or failed), is handed back for
    the move to set and place; the stop switch goes off. None when the stop isn't off or
    there's no such row (nothing changes). Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        if not stop_off(session, run_id, position):
            return None
        side = list(session.scalars(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.group_position == position)))  # fmt: skip
        if held_back(side):
            return None  # an earlier close or stop move may still be live: never a second
        rows = [r for r in side if r.kind == "stop"]
        stop = max(rows, key=lambda r: r.id, default=None)
        if stop is None or stop.state not in ("cancelled", "failed"):
            return None
        if unclear_send(stop):
            return None  # its last send was Unclear: it may be live at the broker
        last = stop.log[-1].get("event") if stop.log else None
        if (
            stop.state == "cancelled"
            and last != EXITS_OFF
            and not str(last or "").startswith(("exits are off", "the stop is off"))
        ):
            return None  # not known gone: never a second stop
        override = session.scalar(_override_of(run_id, position))
        override.stop_off = False
        override.log = [*(override.log or []), {"at": deps.now().isoformat(),
                        "what": "stop on", "result": f"{STOP_BACK_ON}: {why}"}]  # fmt: skip
        override.changed_at = deps.now()
        _log(stop, deps, event=f"{STOP_BACK_ON}: {why}")
        session.commit()
        return stop.id


def unclear_send(stop: BotExit) -> bool:
    """A "failed" stop whose last send was answered Unclear (rearm and place_protections
    write "failed" for any refusal): it may be working at the broker, so it's never
    known gone and nothing may place a second stop over it."""
    if stop.state != "failed":
        return False
    sent = [e.get("result") for e in stop.log or () if isinstance(e, dict) and e.get("result")]
    return bool(sent) and str(sent[-1]).startswith(orders.UNCLEAR)


def place_revived(deps, stop_id: int, trigger) -> tuple[bool, str]:
    """A switched-off stop being switched back on, placed at `trigger` (already checked
    against the market) by rearm(first=True) directly, as Restore does: a refusal (or
    Freeze coming on) leaves it "failed", never "pending" for the monitor, whose pending
    placement has no live check. Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        old = stop.trigger
        stop.trigger, stop.state, stop.broker_order_id = trigger, "failed", None
        session.commit()
    placed = rearm(deps, stop_id, first=True)
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        if stop.state == "pending":
            stop.state = "failed"
            _log(stop, deps, event="not placed: orders are frozen")
            session.commit()
        state = stop.state
    if placed:
        return True, f"stop {old} → {trigger}"
    if state == "expired":
        return True, "stop not needed: the side's position is gone"
    return False, (
        f"the stop at {trigger} wasn't placed: that side has no stop, and nothing places it "
        "later on its own. Set it from the run page"
    )


def unrevive_stop(deps, run_id: int, position: int, stop_id: int, why: str) -> None:
    """revive_stop undone when the move it was for didn't happen: the stop is off again,
    as you left it, and its row is cancelled."""
    with deps.session_factory() as session:
        override = session.scalar(_override_of(run_id, position))
        override.stop_off = True
        stop = session.get(BotExit, stop_id)
        stop.state, stop.broker_order_id = "cancelled", None
        _log(stop, deps, event=EXITS_OFF, why=f"the stop stays off: {why}")
        session.commit()


def _sides_where(session_factory, run_id: int, condition) -> set[int]:
    with session_factory() as session:
        return set(
            session.scalars(
                select(BotTradeOverride.group_position).where(
                    BotTradeOverride.run_id == run_id, condition
                )
            )
        )


def off_sides(session_factory, run_id: int) -> set[int]:
    """The run's sides with both switched off (exits off)."""
    return _sides_where(session_factory, run_id, BotTradeOverride.exits_off)


def target_off_sides(session_factory, run_id: int) -> set[int]:
    """The run's sides with their target switched off."""
    return _sides_where(session_factory, run_id, BotTradeOverride.target_off.is_(True))


def stop_off_sides(session_factory, run_id: int) -> set[int]:
    """The run's sides with their stop switched off."""
    return _sides_where(session_factory, run_id, BotTradeOverride.stop_off.is_(True))


@dataclass(frozen=True)
class _Run:
    """What a run's exits trade with: the run's own account and live setting."""

    id: int
    account: str
    live: bool
    broker: str = "tastytrade"  # 0026: the broker it fired at (bots slice 11)


def _run_and_bot(deps, run_id: int):
    with deps.session_factory() as session:
        row = session.get(BotRun, run_id)
        bot_id, account, live, broker = row.bot_id, row.account_number, row.live, row.broker
    bot = bot_store.load(deps.session_factory, bot_id)
    return _Run(run_id, account or bot.account_number, live, broker or bot.broker), bot


def _request(bot, run: _Run, order: Order) -> orders.Request:
    return orders.Request(
        run.broker, run.account, "bot", order, bot_id=bot.id, bot_run_id=run.id, run_live=run.live
    )


def _log(row: BotExit, deps, **entry) -> None:
    row.log = [*row.log, {"at": deps.now().isoformat(), **entry}]
    row.updated_at = deps.now()


def _legs_show(deps, run: _Run, wanted: dict[str, int]) -> bool:
    """Wait for the broker to show the trade's legs: a closing order is refused
    until it does."""
    for attempt in range(POSITION_TRIES):
        try:
            held = _positions(deps, run)
        except Exception:  # noqa: BLE001 -- unreadable counts as not shown yet
            logger.exception("couldn't read positions before placing exits")
            held = {}
        if all(abs(held.get(plain(s), 0)) >= n for s, n in wanted.items()):
            return True
        if attempt < POSITION_TRIES - 1:
            deps.sleep(1)
    return False


def _held_short(deps, run: _Run, symbol: str, want: int) -> int | None:
    """How many contracts of `symbol` the broker shows held short, waiting for `want`
    (0 when it's flat or long: a buy stop must never add to a long). Returns the latest
    successful read, or None if every read failed."""
    latest = None
    for attempt in range(POSITION_TRIES):
        try:
            held = _positions(deps, run)
            latest = max(-held.get(plain(symbol), 0), 0)
        except Exception:  # noqa: BLE001 -- an unreadable try counts as not read
            logger.exception("couldn't read positions")
        if latest is not None and latest >= want:
            break
        if attempt < POSITION_TRIES - 1:
            deps.sleep(1)
    return latest


def _stop_request(bot, run: _Run, anchor: exit_math.GroupLeg, units: int, trigger: Decimal):
    """A group's resting stop: a single-leg buy-to-close stop-market on its one short,
    `units` x the short's ratio contracts, GTC (exits._place_stop_loss)."""
    order = Order(
        (Leg(anchor.symbol, Side.BUY),),
        units * anchor.ratio,
        False,
        OrderType.STOP,
        trigger=trigger,
        time_in_force=TimeInForce.GTC,
    )
    return _request(bot, run, order)


def app_watches(row: BotExit) -> bool:
    """Whether the app, not the broker, holds this stop: a group the app always watches
    (decision 0039), or one it holds on its short (holds_short)."""
    return bool(row.watched) or holds_short(row)


def _fill_of(fills: dict, symbol: str) -> Decimal | None:
    fill = fills.get(symbol)
    return Decimal(fill["fill_price"]) if fill and fill.get("fill_price") is not None else None


def place_protections(deps, run_id: int, live_check: bool = False) -> None:
    """After an entry fills: per group, what `exit_plan.plan_group` says for this fill
    (decision 0048): a stop resting on its short, or held by the app on the short's quote
    (`app_held`, `WATCHED_SHORT`) or on the group's P&L (decision 0039); a target armed and
    fired by the watch pass, or resting at TastyTrade as one GTC closing limit for the group
    (`_send_target`). What the fill drops or ignores is noted on the group's row and said in
    one alert (`exit_plan.entry_alert`). Does nothing if the run already has exits, so it
    never places a second set (exits.place_group_protections).

    A resting stop is a single-leg buy stop on the group's one short, sized units x its
    ratio (exits._place_stop_loss), at TastyTrade or Schwab alike (decision 0060).
    A stop on the group's P&L is written "watching" and nothing is sent. Every row records
    its legs per unit and `contracts` in group units.

    The rows are written first, as "placing", and only then is each resting stop sent:
    a crash part-way leaves rows the monitor flags, never a silent gap. A stop
    whose trigger can't be used (stops.stop_calc's problem), or a target that
    can't be reached, is written "failed" with its arithmetic, never sent, and
    alerted (exits._place_stop_loss's backstops).

    `live_check` (a trade handed to the bot, bots slice 12): just before each resting
    stop is sent (or watched), its short's fresh ask is read again; a trade already
    through the stop, or with no fresh ask, gets no stop (nothing is sent), the run is
    marked Needs you and it's alerted (management._group_stop_live_check). A watched
    group's level is checked the same way against the group's live P&L (slice 15)."""
    with deps.session_factory() as session:
        if session.scalar(select(BotExit.id).where(BotExit.run_id == run_id).limit(1)):
            return
        row = session.get(BotRun, run_id)
        legs, contracts = list(row.legs), row.contracts
        fills = {
            f["symbol"]: Decimal(f["fill_price"])
            for f in row.filled_legs
            if f.get("fill_price") is not None
        }
        # The whole trade's credit per unit: its fill, else its order price
        # (exits._entry_credit_for_basis).
        entry_credit = row.fill_credit if row.fill_credit is not None else row.target_credit
        pt_only = bool(row.pt_only)
    run, bot = _run_and_bot(deps, run_id)
    if pt_only:  # bots slice 17: a futures run rests its profit targets and nothing else
        _place_resting_targets(deps, run_id, run, bot, legs, contracts, fills, entry_credit)
        return
    groups = {g.position: g for g in bot.groups}
    names = {g.position: g.name for g in bot.groups}
    whole = [exit_math.GroupLeg(leg["symbol"], Side(leg["side"]), int(leg.get("ratio", 1)))
             for leg in legs]  # fmt: skip
    # Decision 0048: each group's exits as this fill reads them (exit_plan), from the bot as
    # it is now, at the fill
    credit = exit_plan.credit_fill(entry_credit, exit_math.group_entry(whole, fills))
    plans = {}
    for position in sorted({leg["group"] for leg in legs}):
        group = groups.get(position)
        if group is None:
            continue
        own, _ = exit_math.group_legs(legs, position)
        plan = exit_plan.plan_group(group, (leg.side for leg in own), credit, run.broker,
                                    bot.stop_co_mode)  # fmt: skip
        plans[position] = exit_plan.with_pairing(plan, group, names)
    now = deps.now()
    stop_rows, target_rows, resting_rows, unpriced, refused, dropped = [], [], [], [], [], []
    levels: dict[int, list] = {}  # position -> [the stop's calc, the target's close-at]
    through = []  # groups already through their stop by the send (live_check)
    watching = 0  # stops the app watches on a group's P&L
    # A trade handed over (slice 15): each watched level against the group's live P&L,
    # read here, before the session below opens, so its quote waits never hold it.
    late = _watched_late(deps, legs, fills, contracts, plans) if live_check else {}
    with deps.session_factory() as session:
        for position in sorted({leg["group"] for leg in legs}):
            group, plan = groups.get(position), plans.get(position)
            own, gcd = exit_math.group_legs(legs, position)
            on_short = plan.on_short if plan is not None else (
                credit and exit_math.anchor_of(own) is not None
            )  # fmt: skip
            anchor = exit_math.anchor_of(own) if on_short else None
            # Decision 0048: its target rests, so the app holds its stop on the short
            by_app = plan is not None and plan.stop_on == "short"
            other = next((leg for leg in own if leg != anchor), None)
            size = contracts * gcd  # the group's units
            base = dict(
                run_id=run_id,
                group_position=position,
                short_symbol=anchor.symbol if anchor else "",
                long_symbol=other.symbol if other else "",
                legs=[{"symbol": g.symbol, "side": g.side.value, "ratio": g.ratio} for g in own],
                anchor_symbol=anchor.symbol if anchor else None,
                watched=anchor is None,
                contracts=size,
                rearms=0,
                created_at=now,
                updated_at=now,
            )  # fmt: skip
            entry = exit_math.group_entry(own, fills)
            stopped = False  # this group's stop got a usable trigger
            first_row = None  # the group's first row: its note goes there
            levels[position] = [None, None]
            if plan is not None and plan.stop_on is not None:
                stop = BotExit(kind="stop", state="placing", log=[], app_held=by_app, **base)
                log = {"at": now.isoformat(), "stop": f"{plan.group.stop_value:f}"}
                calc = exit_plan.stop_for(plan, own, fills, entry_credit, bot.stop_co_mode,
                                          bot.stop_co_amount)  # fmt: skip
                levels[position][0] = calc
                if calc is not None:
                    log |= calc.as_log()
                    stop.trigger = calc.trigger
                    stopped = calc.problem is None
                    if calc.problem is not None:
                        stop.state = "failed"  # never sent: it would fire at once, or isn't a stop
                        refused.append((group.name, calc.problem))
                    elif anchor is None and late.get(position):
                        # A trade handed over (slice 15): already through the watched level
                        # by now, so it isn't watched (it would close at once); you're told.
                        stop.state = "failed"
                        stopped = False
                        through.append(f"{group.name}: {late[position]}")
                        levels[position][0] = None  # C2b: it has no stop: none is said
                    elif anchor is None:
                        # Watched by the app on the group's P&L: nothing rests at the broker,
                        # before or after the legs show (its close sizes itself when it fires).
                        stop.state = WATCHING
                stop.log = [log]
                if stop.state == WATCHING:
                    stop.log = [log, {"at": now.isoformat(), "result": WATCHED_GROUP}]
                    watching += 1
                session.add(stop)
                first_row = stop
                if stop.state == "placing":
                    stop_rows.append((stop, anchor, size, group.name, by_app))
            if entry is None and group is not None:
                missing = ", ".join(g.symbol.strip() for g in own if g.symbol not in fills)
                unpriced.append((group.name, missing, stopped))
            if plan is not None and plan.group.target_value is not None:
                target = BotExit(kind="target", state="placing", log=[], **base)
                log = {"at": now.isoformat(), "target": f"{plan.group.target_value:f}",
                       "unit": plan.group.target_unit}  # fmt: skip
                if plan.rests == "target":
                    log["rests"] = "target"  # rested() reads it (decision 0048)
                if entry is not None:
                    limit, problem = exit_plan.target_for(plan, own, fills)
                    log |= {"credit": f"{entry:f}", "close_at": f"{limit:f}"}
                    if problem is None:
                        target.limit = limit
                        levels[position][1] = limit
                    else:
                        target.state = "failed"
                        log["problem"] = problem
                        dropped.append((group.name, problem))
                target.log = [log]
                session.add(target)
                first_row = first_row or target
                if target.state == "placing":
                    rests = plan.rests == "target" and target.limit is not None
                    (resting_rows if rests else target_rows).append((target, own, size, group.name))
            notes = () if plan is None else plan.notes
            if plan is not None and plan.fallback:
                notes = (exit_plan.FALLBACK_NOTE, *notes)  # spec section 3, on the run page
            if notes and first_row is not None:
                # what the fill changed for this group: the run page shows it on the group
                first_row.log = [*first_row.log, {"at": now.isoformat(),
                                                  "note": " ".join(notes)}]  # fmt: skip
        session.commit()
        stop_rows = [(r.id, a, n, r.trigger, name, by_app)
                     for r, a, n, name, by_app in stop_rows]  # fmt: skip
        target_rows = [(r.id, r.limit, r.group_position) for r, *_ in target_rows]
        resting_rows = [(r.id, own, n, r.limit, name) for r, own, n, name in resting_rows]
    try:
        # A fresh bid is there when a stop fills and the wing is sold, and a fresh ask on
        # the short when a time action moves its stop (the live check).
        deps.hub.subscribe({leg["symbol"] for leg in legs})
    except Exception:  # noqa: BLE001 -- the wing still sells without one (a one-tick limit)
        logger.exception("couldn't subscribe the wings' quotes")
    # Each leg as the run holds it: the run's contracts x the leg's own bot ratio.
    wanted = {leg["symbol"]: contracts * leg["ratio"] for leg in legs}
    shown = _legs_show(deps, run, wanted)
    failed = held = 0
    # Every stop of this run is the app's (every group watched on its P&L, or held on its
    # short while its target rests): nothing waits on the legs showing at the broker.
    app_only = (watching > 0 or bool(stop_rows)) and all(row[5] for row in stop_rows)
    for exit_id, anchor, units, trigger, name, by_app in stop_rows:
        app = by_app
        live = live_check and trigger is not None and anchor is not None
        problem = _through(deps, anchor.symbol, trigger) if live else None
        if trigger is None or anchor is None or (not shown and not app):
            state, message, order_id = "failed", "not sent", None
        elif problem is not None:
            state, message, order_id = "failed", f"not sent: {problem}", None
            through.append(f"{name}: {problem}")
        elif app:
            # Decision 0048 (its target rests): the app watches it on the short's quote;
            # nothing goes to the broker, before or after the legs show. Its close is sized
            # to what's held when it fires.
            state, message, order_id = WATCHING, watched_short(run), None
        else:
            result = orders.submit(
                deps.session_factory,
                deps.clients(),
                _stop_request(bot, run, anchor, units, trigger),
                now=now,
                notify=deps.notify,
            )
            message = result.message
            if _placed(result):
                state, order_id = "working", result.broker_order_id
            else:
                state, order_id = ("pending" if _held_for_freeze(result) else "failed"), None
        failed += state == "failed" and shown and trigger is not None
        held += state == "pending"
        with deps.session_factory() as session:
            stop = session.get(BotExit, exit_id)
            stop.state, stop.broker_order_id = state, order_id
            stop.log = [*stop.log, {"at": now.isoformat(), "result": message}]
            session.commit()
    with deps.session_factory() as session:
        for exit_id, limit, position in target_rows:
            target = session.get(BotExit, exit_id)
            target.state = "armed" if shown and limit is not None else "failed"
            if target.state == "failed":
                levels[position][1] = None
        session.commit()
    unplaced, targets_held = [], 0  # unplaced: (position, group name, why)
    for exit_id, own, units, limit, name in resting_rows:
        # Decision 0048: one GTC closing limit for the whole group, through the order path
        state, order_id, rests_at, message = _send_target(deps, bot, run, own, units, limit, shown)
        targets_held += state == "pending"
        unclear = sent_unclear(state, message)
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            position = row.group_position
            row.state, row.broker_order_id = state, order_id
            if rests_at is not None:
                row.limit = rests_at
                levels[position][1] = rests_at
            _log(row, deps, result=message)
            session.commit()
        if state not in ("working", "pending"):
            levels[position][1] = None  # C2a: nothing rests, so no alert says it does
        if state == "failed" and shown and not unclear:
            unplaced.append((position, name, message))
        if unclear:  # it may rest at TastyTrade: held, never a second closing order
            _hold_unclear_send(deps, bot, run_id, position, exit_id, "placing its profit target")
    if not shown and app_only:
        _alert(
            deps,
            f"{bot.name}: profit targets not set",
            f"The broker didn't show the trade's legs, so its profit targets weren't set. Its "
            f"stops are {WATCHED}. Check your broker.",
            bot.id,
            run_id=run_id,
        )
    elif not shown:
        detail = "The broker didn't show the trade's legs, so no stop was placed."
        resting = [name for _, anchor, _, _, name, by_app in stop_rows
                   if anchor is not None and not by_app]  # fmt: skip
        # a mixed bot: its other groups' stops are the app's, watching (M4)
        if resting and (watching or any(row[5] for row in stop_rows)):
            which = ", ".join(dict.fromkeys(resting))
            detail = (
                f"The broker didn't show the trade's legs, so the stop for {which} wasn't "
                f"placed at the broker. The other groups' stops are {WATCHED}."
            )
        _alert(deps, f"{bot.name}: exits not placed", f"{detail} Check your broker.", bot.id,
               run_id=run_id)  # fmt: skip
    for name, missing, stopped in unpriced:
        lacks = "no profit target; its stop stands" if stopped else "no stop"
        _alert(
            deps,
            f"{bot.name}: exits not placed",
            f"The broker gave no fill price for {missing}, so the exits of {name} weren't "
            f"set. {name} has {lacks}. Check your broker.",
            bot.id,
            run_id=run_id,
        )
    for name, problem in refused:
        _alert(
            deps,
            f"{bot.name}: a stop was not placed",
            f"{name}: {problem}. {name} has no stop. Check your broker.",
            bot.id,
            run_id=run_id,
        )
    for name, problem in dropped:
        _alert(
            deps,
            f"{bot.name}: profit target not set",
            f"{name}: {problem}. {name} has no profit target; its stop stands.",
            bot.id,
            run_id=run_id,
        )
    if failed:
        _alert(
            deps,
            f"{bot.name}: a stop was not placed",
            "The order path didn't place a stop (see the Orders log). Check your broker.",
            bot.id,
            run_id=run_id,
        )
    if through:
        detail = (
            f"The trade is already through its stop: {'; '.join(through)}. That group has no "
            "stop. Check your broker."
        )
        _alert(deps, f"{bot.name}: a stop was not placed", detail, bot.id, run_id=run_id)
        bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    if held:
        _alert(
            deps,
            f"{bot.name}: stops waiting for Freeze",
            "Orders are frozen, so the stops weren't sent. They're placed when you turn "
            "Freeze off; until then that trade has no stop.",
            bot.id,
            run_id=run_id,
        )
    for position, name, message in unplaced:
        _no_target_alert(
            deps, bot, run_id, position, "profit target not placed",
            f"{name}: {message}. Its profit target isn't resting at {broker_name(run)}.",
            " Restore missing exits on the run page places it again.",
        )  # fmt: skip
    if targets_held:
        _alert(
            deps, f"{bot.name}: profit targets waiting for Freeze",
            "Orders are frozen, so the profit targets weren't sent. They're placed when you "
            "turn Freeze off; until then the stop is the only exit.", bot.id, run_id=run_id,
        )  # fmt: skip
    said = exit_plan.entry_alert(
        bot.name, entry_credit,
        [(names[p], plans[p], *levels.get(p, [None, None])) for p in sorted(plans)],
        several=len(plans) > 1, broker=broker_name(run),
    )  # fmt: skip
    if said is not None:
        _alert(deps, said[0], said[1], bot.id, run_id=run_id)


# Bots slice 17 (decision 0043): a futures run places its profit target only.
FUTURES_NOTE = "Futures: profit target only, no stop"  # adopt.FUTURES_NOTE
RESTS = "rests at TastyTrade"


def rests_words(run) -> str:
    """ "rests at TastyTrade" or "rests at Schwab": the run's broker (decision 0060)."""
    return f"rests at {broker_name(run)}"


def run_broker_name(deps, run_id: int) -> str:
    """The broker a run fired at, as it's said (broker_name) from its id."""
    with deps.session_factory() as session:
        row = session.get(BotRun, run_id)
        return broker_name(row) if row is not None else "your broker"


def _dollars(run_row: BotRun, points) -> str:
    return futures.dollars(points, run_row.multiplier)


def pt_only(session, run_id: int) -> bool:
    """Whether the run places its profit target only (a futures run, exits.run_is_pt_only;
    a remainder tranche copies its root's)."""
    return bool(session.scalar(select(BotRun.pt_only).where(BotRun.id == run_id)))


def _resting_target(bot, run: _Run, legs, units: int, limit: Decimal, tables) -> Order:
    """A group's resting profit target (exits._place_close_limit): every leg of the group
    closed at its ratio, `units` of it, GTC, at the group's close value as a signed price
    (closing a credit pays a debit), rounded toward a fill on the order's own tick
    (_round_for_order_or_fallback)."""
    order = Order(_closing_legs(legs), units, False, price=-limit, time_in_force=TimeInForce.GTC)
    return ticks.rounded(order, tables)


def _place_resting_targets(deps, run_id, run: _Run, bot, legs, contracts, fills, credit) -> None:
    """A futures run's exits (spec: bots slice 17, section 7; Tracker2's analyzer futures
    path: "A futures run places PT only, whatever stop the strategy has configured"):
    per group, one GTC closing limit at its profit target, resting at the broker
    (exits._place_close_limit: with no stop resting, TastyTrade takes it). No stop row is
    written. The rows are written "placing" first, as every exit is. A target refused by
    Freeze waits as "pending" for the monitor; one that can't be priced or placed is
    "failed" and alerted: that group then has no exit at all."""
    now = deps.now()
    groups = {g.position: g for g in bot.groups}
    rows, failed = [], []
    with deps.session_factory() as session:
        for position in sorted({leg["group"] for leg in legs}):
            group = groups.get(position)
            own, gcd = exit_math.group_legs(legs, position)
            name = group.name if group is not None else f"Group {position + 1}"
            if group is None or group.target_value is None:
                failed.append((name, "the bot sets no profit target on it"))
                continue
            anchor = exit_math.anchor_of(own) if exit_plan.credit_fill(credit) else None
            other = next((leg for leg in own if leg != anchor), None)
            log = {"at": now.isoformat(), "target": f"{group.target_value:f}",
                   "unit": group.target_unit, "note": FUTURES_NOTE}  # fmt: skip
            target = BotExit(
                run_id=run_id, group_position=position, kind="target", state="placing",
                short_symbol=anchor.symbol if anchor else "",
                long_symbol=other.symbol if other else "",
                legs=[{"symbol": g.symbol, "side": g.side.value, "ratio": g.ratio} for g in own],
                anchor_symbol=None, watched=False, contracts=contracts * gcd, rearms=0,
                created_at=now, updated_at=now,
            )  # fmt: skip
            entry = exit_math.group_entry(own, fills)
            if entry is None:
                target.state = "failed"
                missing = ", ".join(g.symbol.strip() for g in own if g.symbol not in fills)
                log["problem"] = f"no fill price for {missing}"
            else:
                limit, problem = stops.target_limit(group, entry)
                log |= {"credit": f"{entry:f}", "close_at": f"{limit:f}"}
                if problem is not None:
                    target.state = "failed"
                    log["problem"] = problem
                else:
                    target.limit = limit
            target.log = [log]
            session.add(target)
            session.flush()
            if target.state == "failed":
                failed.append((name, log["problem"]))
            else:
                rows.append((target.id, own, contracts * gcd, limit, name))
        session.commit()
    wanted = {leg["symbol"]: contracts * leg["ratio"] for leg in legs}
    shown = _legs_show(deps, run, wanted) if rows else True
    held = 0
    for exit_id, own, units, limit, name in rows:
        state, order_id, rested, message = _send_target(deps, bot, run, own, units, limit, shown)
        held += state == "pending"
        unclear = sent_unclear(state, message)
        if state == "failed" and not unclear:
            failed.append((name, message))
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            row.state, row.broker_order_id = state, order_id
            if rested is not None:
                row.limit = rested
            _log(row, deps, result=message)
            position = row.group_position
            session.commit()
        if unclear:  # it may rest at TastyTrade: held, never a second closing order
            _hold_unclear_send(deps, bot, run_id, position, exit_id, "placing its profit target")
    for name, problem in failed:
        _alert(
            deps, f"{bot.name}: profit target not placed",
            f"{name}: {problem}. {FUTURES_NOTE}, so {name} has no exit at all. Check your "
            "broker.", bot.id, run_id=run_id,
        )  # fmt: skip
    if failed:
        bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: no profit target "
                             f"on {', '.join(name for name, _ in failed)}.")  # fmt: skip
    if held:
        _alert(
            deps, f"{bot.name}: profit targets waiting for Freeze",
            "Orders are frozen, so the profit targets weren't sent. They're placed when you "
            f"turn Freeze off; until then this futures trade has no exit. {FUTURES_NOTE}.",
            bot.id, run_id=run_id,
        )  # fmt: skip


def _send_target(deps, bot, run: _Run, legs, units: int, limit: Decimal, shown: bool):
    """(state, order id, the price it rests at, what happened) for one resting target."""
    if not shown:
        return "failed", None, None, "not sent: the broker didn't show the trade's legs"
    try:
        probe = Order(_closing_legs(legs), units, False, price=-limit,
                      time_in_force=TimeInForce.GTC)  # fmt: skip
        tables = orders.tick_tables(deps.session_factory, deps.clients(), probe, deps.now())
        order = _resting_target(bot, run, legs, units, limit, tables)
    except Exception as e:  # noqa: BLE001 -- an OrderError (rounds to 0) or a bad table
        logger.exception("couldn't price run %s's profit target", run.id)
        return "failed", None, None, f"not sent: it can't be priced ({e})"
    result = orders.submit(
        deps.session_factory, deps.clients(), _request(bot, run, order), now=deps.now(),
        notify=deps.notify,
    )  # fmt: skip
    rested = -order.price
    if _placed(result):
        return "working", result.broker_order_id, rested, f"{rests_words(run)}: {result.message}"
    if _held_for_freeze(result):
        return "pending", None, rested, result.message
    if result.message.startswith(orders.UNCLEAR):
        return "failed", None, None, result.message  # it may rest: sent_unclear, held
    return "failed", None, None, f"not sent: {result.message}"


def sent_unclear(state: str, message: str) -> bool:
    """A resting target's send (_send_target) the broker gave no clear answer to: the order
    may rest at TastyTrade, so its row is held (_hold_unclear_send), never just failed."""
    return state == "failed" and str(message).startswith(orders.UNCLEAR)


NO_EXIT_NOW = "It has no stop, so it has no exit now."
FUTURES_NO_EXIT_NOW = "This futures trade has no exit now."


def left_without_target(deps, run_id: int, position: int) -> tuple[str, bool]:
    """(what still protects the group, in words; whether nothing does) once its resting
    profit target isn't at TastyTrade (final review C2c): a futures trade has no exit; a
    group with no live stop has none either; else its stop, as it stands."""
    with deps.session_factory() as session:
        if pt_only(session, run_id):
            return FUTURES_NO_EXIT_NOW, True
        stop = session.scalar(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.group_position == position,
            BotExit.kind == "stop", BotExit.state.in_(STOP_LIVE),
        ).order_by(BotExit.id.desc()).limit(1))  # fmt: skip
        state = stop.state if stop is not None else None
    if state is None:
        return NO_EXIT_NOW, True
    if state == "working":
        return f"Its stop still rests at {run_broker_name(deps, run_id)}.", False
    if state == "pending":
        return "Its stop waits for Freeze to go off, so it has no exit until then.", True
    return "Its stop is still watched by the app.", False


def _no_target_alert(deps, bot, run_id: int, position: int, title: str, said: str,
                     tail: str = "") -> None:  # fmt: skip
    """A group's resting target that isn't at TastyTrade any more, alerted with what's left
    (left_without_target); Needs you when nothing is."""
    left, none = left_without_target(deps, run_id, position)
    detail = f"{said} {left}{tail} Check your broker."
    if none:
        bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    _alert(deps, f"{bot.name}: {title}", detail, bot.id, run_id=run_id)


def _place_pending_target(deps, exit_id: int) -> None:
    """A futures target Freeze held back, placed once it's off (the stops' "pending",
    monitor_pass). It's sent only while the broker still shows the group's legs."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        if row.state != "pending":
            return
        run_id, units, limit = row.run_id, row.contracts, row.limit
        legs = exit_math.row_legs(row)
        futures = pt_only(session, run_id)
    run, bot = _run_and_bot(deps, run_id)
    shown = _legs_show(deps, run, {leg.symbol: units * leg.ratio for leg in legs})
    state, order_id, rested, message = _send_target(deps, bot, run, legs, units, limit, shown)
    if state == "pending":
        return  # still frozen
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        row.state, row.broker_order_id = state, order_id
        if rested is not None:
            row.limit = rested
        _log(row, deps, result=message)
        session.commit()
    if sent_unclear(state, message):  # it may rest at TastyTrade: held
        _hold_unclear_send(deps, bot, run_id, row.group_position, exit_id,
                           "placing its profit target once Freeze was off")  # fmt: skip
        return
    if state == "failed":
        name = _group_name(bot, row.group_position)
        if futures:
            detail = f"{name}: {message}. {FUTURES_NOTE}, so {name} has no exit at all. "
            _alert(deps, f"{bot.name}: profit target not placed", f"{detail}Check your broker.",
                   bot.id, run_id=run_id)  # fmt: skip
        else:  # decision 0048: what's left is said as it is (C2c)
            _no_target_alert(deps, bot, run_id, row.group_position, "profit target not placed",
                             f"{name}: {message}.")  # fmt: skip


def rested(row: BotExit) -> bool:
    """A target placed to rest at the broker (decision 0048's choice, or a futures run's),
    never one the app arms: it's working, waiting for Freeze, or it was placed so."""
    return row.kind == "target" and (
        row.state in ("working", "pending")
        or any(isinstance(e, dict) and e.get("rests") == "target" for e in row.log or ())
    )


def target_rested(session, run_id: int, position: int) -> bool:
    """Whether the group's latest target rests (or rested) at the broker."""
    rows = [r for r in session.scalars(select(BotExit).where(
        BotExit.run_id == run_id, BotExit.group_position == position,
        BotExit.kind == "target"))]  # fmt: skip
    latest = max(rows, key=lambda r: r.id, default=None)
    return latest is not None and rested(latest)


def _end_the_watch(session, deps, run_id: int, position: int, why: str) -> None:
    """The group closed by its resting target (decision 0048): the stop the app watches stops
    watching, and one Freeze held back is never placed. Nothing else to send."""
    for stop in session.scalars(select(BotExit).where(
        BotExit.run_id == run_id, BotExit.group_position == position, BotExit.kind == "stop",
        BotExit.state.in_(("pending", WATCHING)),
    )):  # fmt: skip
        stop.state, stop.broker_order_id = "cancelled", None
        _log(stop, deps, event=why)


# --- Bots slice 21: a futures trade's resting target from the run page --------------------

CHANGING_TARGET = "cancelling the resting target to change it"  # recovery's marker
TARGET_UNCLEAR = "unclear: a change was cut off or unconfirmed; a target may rest at the broker"


def _resting_row(session, run_id: int, position: int) -> BotExit | None:
    """The group's resting target: "working" at the broker, or "pending" for Freeze."""
    rows = [r for r in session.scalars(select(BotExit).where(
        BotExit.run_id == run_id, BotExit.group_position == position, BotExit.kind == "target"))
        if r.state in ("working", "pending")]  # fmt: skip
    return max(rows, key=lambda r: r.id, default=None)


def _target_filled(session, run_id: int, position: int) -> bool:
    return session.scalar(select(BotExit.id).where(
        BotExit.run_id == run_id, BotExit.group_position == position,
        BotExit.kind == "target", BotExit.state == "filled")) is not None  # fmt: skip


ALREADY_FILLED = "its target had already filled: the group is closed"


def _held_units_of(deps, run: _Run, legs, cap: int) -> int:
    """The whole units of the group the broker holds the group's way, capped at `cap`;
    `cap` when positions can't be read (the order path then checks what's held)."""
    try:
        held = _positions(deps, run)
    except Exception:  # noqa: BLE001
        logger.exception("couldn't read positions to size run %s's target", run.id)
        return cap
    units = [max(held.get(plain(leg.symbol), 0) * (1 if leg.side is Side.BUY else -1), 0)
             // leg.ratio for leg in legs]  # fmt: skip
    return min([cap, *units])


def _hold_target(deps, bot, run_id: int, position: int, exit_id: int, why: str,
                 doing: str = "changing its profit target",
                 title: str = "profit target change unclear") -> str:  # fmt: skip
    """A target change (or send: `doing`) that may have left an order resting the app
    doesn't know: the row held back (an "unclear" event: target_may_rest, so no close is
    sent on the group, and Restore and changes refuse), Needs you, alerted."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        row.state = "failed"
        _log(row, deps, event=TARGET_UNCLEAR, why=why)
        session.commit()
    name = _group_name(bot, position)
    detail = (
        f"{name}: {doing} {why}. A target may rest at {run_broker_name(deps, run_id)} that the "
        "app can't vouch for, so it won't place another, and no close is sent on this group "
        "while a profit target may still rest there. Check your broker."
    )
    bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    _alert(deps, f"{bot.name}: {title}", detail, bot.id, run_id=run_id)
    return detail


def _hold_unclear_send(deps, bot, run_id: int, position: int, exit_id: int,
                       doing: str) -> str:  # fmt: skip
    """Final review C1: a resting target's send that got no clear answer (sent_unclear),
    held as an unclear change is: it may rest at TastyTrade."""
    return _hold_target(deps, bot, run_id, position, exit_id,
                        f"got no clear answer from {run_broker_name(deps, run_id)}", doing=doing,
                        title="profit target unclear")  # fmt: skip


def change_resting_target(deps, run_id: int, position: int,
                          limit: Decimal) -> tuple[bool, str]:  # fmt: skip
    """A group's resting target (a futures run's, or one chosen to rest: decision 0048)
    moved to `limit` (bots slice 21; Tracker2's
    management.apply_override, which cancels the working target before placing the new
    one, and never re-prices a part-filled order in place, execution's rule). Under the
    lock: the order read, and refused unless it's working with nothing filled; marked
    (CHANGING_TARGET, recovery's), cancelled through the order path and the cancel read
    back; then the new target placed, sized to what the broker holds. An unclear cancel
    holds the group back; a new target not placed is alerted: the trade has no exit."""
    with EXITS_LOCK:
        with deps.session_factory() as session:
            row = _resting_row(session, run_id, position)
            if row is None:
                if _target_filled(session, run_id, position):
                    return False, ALREADY_FILLED
                return False, "this group has no resting target to change"
            exit_id, state, order_id = row.id, row.state, row.broker_order_id
            units, legs, old = row.contracts, exit_math.row_legs(row), row.limit
            cols = {
                "short_symbol": row.short_symbol, "long_symbol": row.long_symbol,
                "legs": list(row.legs or []), "anchor_symbol": None, "watched": False,
            }  # fmt: skip
        run, bot = _run_and_bot(deps, run_id)
        try:
            probe = Order(_closing_legs(legs), units, False, price=-limit,
                          time_in_force=TimeInForce.GTC)  # fmt: skip
            tables = orders.tick_tables(deps.session_factory, deps.clients(), probe, deps.now())
            rests = -_resting_target(bot, run, legs, units, limit, tables).price
        except Exception as e:  # noqa: BLE001 -- an OrderError (rounds to 0) or a bad table
            logger.exception("couldn't price run %s's new target", run_id)
            return False, f"the new target can't be priced ({e}); the old one still rests"
        if state == "pending" or not order_id:
            with deps.session_factory() as session:
                row = session.get(BotExit, exit_id)
                row.limit = rests
                _log(row, deps, event=f"changed by you: {old} → {rests} (placed after Freeze)")
                session.commit()
            _frozen_gap(deps, bot, run_id, position, rests)
            return True, f"target {old} → {rests}, placed when Freeze is off"
        try:
            found = _broker(deps, run).get_order(run.account, order_id)
        except Exception:  # noqa: BLE001 -- nothing touched
            logger.exception("couldn't read run %s's target before changing it", run_id)
            return False, "its order couldn't be read; nothing was changed"
        if found.status is OrderStatus.FILLED:
            return False, ALREADY_FILLED
        if not found.may_be_working:
            return False, (
                f"its order isn't working at {broker_name(run)} any more; nothing was changed"
            )
        if any(leg.remaining < leg.quantity for leg in found.legs):
            return False, f"part of its target has filled: change it at {broker_name(run)}"
        with deps.session_factory() as session:
            _log(session.get(BotExit, exit_id), deps, event=CHANGING_TARGET, to=f"{rests:f}")
            session.commit()

        def stands(note: str) -> tuple[bool, str]:
            with deps.session_factory() as session:
                _log(session.get(BotExit, exit_id), deps, event=f"not changed: {note}")
                session.commit()
            return False, f"{note}; nothing was changed"

        sent = orders.cancel(deps.session_factory, deps.clients(), run.broker, run.account,
                             order_id, "bot", bot.id, now=deps.now(),
                             notify=deps.notify)  # fmt: skip
        if sent.action == "refuse":
            return stands(f"its cancel wasn't sent ({sent.message}); the old target still rests")
        try:
            found = read_after_cancel(
                deps, lambda: _broker(deps, run).get_order(run.account, order_id)
            )
        except Exception:  # noqa: BLE001 -- can't tell: held
            logger.exception("couldn't read run %s's target back after its cancel", run_id)
            return False, _hold_target(deps, bot, run_id, position, exit_id,
                                       "couldn't be read back after its cancel")  # fmt: skip
        if found.status is OrderStatus.FILLED:
            stands("it filled first")
            return False, ALREADY_FILLED
        if found.may_be_working:
            if not sent.ok and not sent.message.startswith(orders.UNCLEAR):
                # the broker plainly turned the cancel down: the old target still rests
                return stands(f"its cancel was turned down ({sent.message}); it still rests")
            return False, _hold_target(deps, bot, run_id, position, exit_id,
                                       "got no confirmed cancel")  # fmt: skip
        filled, _ = filled_units(found, {plain(leg.symbol): leg.ratio for leg in legs})
        size = _held_units_of(deps, run, legs, units - filled)
        now = deps.now()
        with deps.session_factory() as session:
            # The old row's cancel and the new row ("placing", no order yet) in one commit:
            # a crash before the new one is recorded leaves a row recovery holds back
            # (_recover_placing_targets), never a group with no record of its exit.
            row = session.get(BotExit, exit_id)
            row.state = "cancelled"
            _log(row, deps, event=f"cancelled to change it ({old} → {rests})")
            new = None
            if size >= 1:
                new = BotExit(
                    run_id=run_id, group_position=position, kind="target", state="placing",
                    **cols, contracts=size, limit=rests, rearms=0,
                    # log[0] is recovery's marker (_recover_placing_targets); then that it
                    # rests (decision 0048), so a failed send is placed again, never armed
                    log=[{"at": now.isoformat(), "event": CHANGE_PLACING, "from": f"{old}"},
                         {"rests": "target"}],
                    created_at=now, updated_at=now,
                )  # fmt: skip
                session.add(new)
            session.commit()
            new_row = new.id if new is not None else None
        if new_row is None:
            return True, "nothing of the group is held, so no target is needed"
        new_state, new_id, rested, message = _send_target(deps, bot, run, legs, size, limit, True)
        with deps.session_factory() as session:
            new = session.get(BotExit, new_row)
            new.state = "failed" if new_state == "failed" else new_state
            new.broker_order_id = new_id
            if rested is not None:
                new.limit = rested
            _log(new, deps, event=f"changed by you: {old} → {rests}", result=message)
            session.commit()
    if new_state == "failed" and orders.UNCLEAR in message:
        return False, _hold_target(deps, bot, run_id, position, new_row,
                                   "got no clear answer placing the new one")  # fmt: skip
    if new_state == "failed":
        name = _group_name(bot, position)
        left, _ = left_without_target(deps, run_id, position)
        detail = (
            f"{name}: its old profit target was cancelled to change it, and the new one at "
            f"{rests} wasn't placed ({message}). {left} Check your broker; Restore missing "
            "exits on the run page places it again."
        )
        bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
        _alert(deps, f"{bot.name}: profit target not placed", detail, bot.id, run_id=run_id)
        return False, detail
    if new_state == "pending":
        _frozen_gap(deps, bot, run_id, position, rests)
        return True, f"target {old} → {rests}: the old one cancelled, the new one after Freeze"
    return True, f"target {old} → {rested} ({rests_words(run)}, order {new_id})"


CHANGE_PLACING = "placing the changed target"  # a changed target's row before its send


def _frozen_gap(deps, bot, run_id: int, position: int, rests) -> None:
    """A futures target changed while Freeze holds it: nothing rests at TastyTrade."""
    name = _group_name(bot, position)
    _alert(
        deps, f"{bot.name}: profit target waits for Freeze",
        f"{name}'s profit target was changed to {rests}, but orders are frozen, so nothing "
        f"rests at TastyTrade: {FUTURES_NOTE}, so this trade has no exit until Freeze is off.",
        bot.id, run_id=run_id,
    )  # fmt: skip


def _recover_placing_targets(deps) -> None:
    """A changed futures target left "placing" (the app died between writing it and
    recording its send): its order may or may not rest at TastyTrade, so the group is held
    back, Needs you, alerted, and nothing is placed. Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        stuck = [
            (r.id, r.run_id, r.group_position)
            for r in session.scalars(select(BotExit).where(
                BotExit.kind == "target", BotExit.state == "placing"))
            if r.log and r.log[0].get("event") == CHANGE_PLACING and not r.broker_order_id
        ]  # fmt: skip
    for exit_id, run_id, position in stuck:
        try:
            _, bot = _run_and_bot(deps, run_id)
            _hold_target(deps, bot, run_id, position, exit_id,
                         "was cut off before the new order was recorded")  # fmt: skip
        except Exception:  # noqa: BLE001 -- one row never stops the others
            logger.exception("couldn't hold changed target %s", exit_id)


def cancel_resting_target(deps, run_id: int, position: int) -> tuple[bool, str]:
    """A group's resting target (a futures run's, or decision 0048's) switched off (bots
    slice 21; Tracker2's
    profit_target_off on a PT-only run): cancelled through the order path and read back;
    only a confirmed cancel turns it off (the abort rule). Freeze's pending one is just
    dropped. Under EXITS_LOCK."""
    with EXITS_LOCK:
        with deps.session_factory() as session:
            row = _resting_row(session, run_id, position)
            if row is None:
                if _target_filled(session, run_id, position):
                    return False, ALREADY_FILLED
                return False, "this group has no resting target to switch off"
            exit_id, state, order_id = row.id, row.state, row.broker_order_id
        run, bot = _run_and_bot(deps, run_id)
        if state == "working" and order_id:
            sent = orders.cancel(
                deps.session_factory, deps.clients(), run.broker, run.account, order_id, "bot",
                bot.id, now=deps.now(), notify=deps.notify,
            )  # fmt: skip
            if sent.action == "refuse":
                return False, f"its cancel wasn't sent ({sent.message}); the target still rests"
            try:
                found = read_after_cancel(
                    deps, lambda: _broker(deps, run).get_order(run.account, order_id)
                )
            except Exception:  # noqa: BLE001 -- can't tell: it stays on, the monitor reads it
                logger.exception("couldn't read target %s back after its cancel", exit_id)
                return False, "it couldn't be read back after its cancel; the target still rests"
            if found.status is OrderStatus.FILLED:
                return False, f"{ALREADY_FILLED}; nothing was switched off"
            if found.may_be_working:
                return False, "its cancel wasn't confirmed; the target still rests"
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            row.state, row.broker_order_id = "cancelled", None if state == "pending" else order_id
            _log(row, deps, event=EXITS_OFF)
            session.commit()
    return True, f"its target was cancelled at {broker_name(run)}"


def place_target_again(deps, run_id: int, position: int, units: int) -> tuple[bool, str]:
    """A resting target you switched off (a futures run's, or decision 0048's), placed
    again at its last limit once the broker
    shows the group's legs (bot_rearm._restore_resting_target's steps). Under EXITS_LOCK."""
    with EXITS_LOCK:
        with deps.session_factory() as session:
            rows = [r for r in session.scalars(select(BotExit).where(
                BotExit.run_id == run_id, BotExit.group_position == position,
                BotExit.kind == "target"))]  # fmt: skip
            latest = max(rows, key=lambda r: r.id, default=None)
            if latest is None or latest.state != "cancelled" or latest.limit is None:
                return False, (
                    f"there's no target to put back; place it at {run_broker_name(deps, run_id)}"
                )
            exit_id, legs, limit = latest.id, exit_math.row_legs(latest), latest.limit
            units = min(latest.contracts, units)
        run, bot = _run_and_bot(deps, run_id)
        shown = _legs_show(deps, run, {leg.symbol: units * leg.ratio for leg in legs})
        state, order_id, rested, message = _send_target(deps, bot, run, legs, units, limit, shown)
        if sent_unclear(state, message):  # it may rest at TastyTrade: held, never again
            return False, _hold_unclear_send(deps, bot, run_id, position, exit_id,
                                             "placing its profit target again")  # fmt: skip
        if state == "failed":
            with deps.session_factory() as session:  # it stays cancelled: recorded why
                _log(session.get(BotExit, exit_id), deps, event="not placed again",
                     result=message)  # fmt: skip
                session.commit()
            return False, f"the target wasn't placed ({message})"
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            row.state, row.broker_order_id, row.contracts = state, order_id, units
            if rested is not None:
                row.limit = rested
            _log(row, deps, result=message, event="placed again: the target is back on")
            session.commit()
    if state == "pending":
        return True, f"its target at {rested} is placed when Freeze is off"
    return True, (
        f"its target rests at {run_broker_name(deps, run_id)} again at {rested} (order {order_id})"
    )


def _group_name(bot, position: int) -> str:
    found = next((g for g in bot.groups if g.position == position), None) if bot else None
    return found.name if found is not None else f"Group {position + 1}"


def _cut_off_change(deps, row: BotExit, bot, found) -> bool:
    """A futures target whose change from the run page was cut off after it was marked
    (CHANGING_TARGET) and its order then reads gone: the new one may or may not rest, so
    it's held like an unclear change (bots slice 21). False when this isn't such a row."""
    if not row.log or row.log[-1].get("event") != CHANGING_TARGET:
        return False
    row.state = "failed"
    _log(row, deps, event=TARGET_UNCLEAR, why="a change was cut off part-way")
    name = _group_name(bot, row.group_position)
    detail = (
        f"{name}: a change to its profit target was cut off part-way. A target may rest at "
        f"{run_broker_name(deps, row.run_id)} that the app can't vouch for, so it won't place "
        "another, and no close is sent on this group while a profit target may still rest "
        "there. Check your broker."
    )
    _alert(deps, f"{bot.name}: profit target change unclear", detail, bot.id, run_id=row.run_id)
    bot_entry.update_run(deps, row.run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    return True


def _target_ended(deps, row: BotExit, bot, status, futures_run: bool = True) -> None:
    """A futures run's resting target ended at the broker without filling: after its
    legs' expiry it simply expired; otherwise (you cancelled or changed it, or it was
    rejected) the trade has no exit now, which is said at once and urgently, and the run
    is marked Needs you. It's never placed again: you may have taken it off on purpose.
    Not a futures run's (decision 0048): its stop is still the app's, which is said, and
    Restore places the target again."""
    with deps.session_factory() as session:
        run_legs = list(session.get(BotRun, row.run_id).legs or [])
    now = deps.now()
    if any(futures.past_expiry(leg.symbol, run_legs, now) for leg in exit_math.row_legs(row)):
        row.state = "expired"
        _log(row, deps, event=f"broker shows {status} after expiry")
        return
    row.state = "failed"
    _log(row, deps, event=f"broker shows {status}")
    name = _group_name(bot, row.group_position)
    if not futures_run:  # decision 0048: what's left is said (C2c); Restore places it again
        _no_target_alert(
            deps, bot, row.run_id, row.group_position, "profit target gone",
            f"The profit target for {name} (order {row.broker_order_id}) ended {status} at "
            "your broker.",
            " The app doesn't place the target again on its own: Restore missing exits on the "
            "run page does.",
        )  # fmt: skip
        return
    detail = (
        f"The profit target for {name} (order {row.broker_order_id}) ended {status} at your "
        "broker. This futures trade has no exits now: the app doesn't place it again. Check "
        "your broker."
    )
    _alert(deps, f"{bot.name}: profit target gone", detail, bot.id, run_id=row.run_id)
    bot_entry.update_run(deps, row.run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")


def _watched_late(deps, legs, fills, contracts: int, plans: dict) -> dict[int, str]:
    """{group position: why its stop on the group's P&L must not be watched now} for the
    groups whose level the live P&L is already at or through (or that can't be read): the
    watched twin of _through, on each group as the fill reads it (exit_plan). Reads quotes
    only; no database session is open."""
    out = {}
    for position, plan in plans.items():
        if plan.stop_on != "pnl":
            continue
        own, gcd = exit_math.group_legs(legs, position)
        entry = exit_math.group_entry(own, fills) if own else None
        calc = None if entry is None else stops.watched_calc(plan.group, entry)
        if calc is None or calc.problem is not None:
            continue  # place_protections refuses it on its own
        g = _Group(tuple(own), None, contracts * gcd, {}, entry, None)
        problem = fires_at_once(calc.trigger, _live_pnl(deps, g))
        if problem is not None:
            out[position] = problem
    return out


def _through(deps, short: str, trigger: Decimal) -> str | None:
    """Why a stop must not go out now (place_protections' live_check), or None."""
    ask = _fresh_ask(deps, short)
    if ask is None:
        return f"no fresh quote for {short.strip()} to show a stop at {trigger} won't fire at once"
    if trigger <= ask:
        return f"a stop at {trigger} is at or below {short.strip()}'s ask {ask}"
    return None


def _bot_of(session, row: BotExit) -> Bot:
    return session.get(Bot, session.get(BotRun, row.run_id).bot_id)


def monitor_pass(deps) -> None:
    """Every 30 s: recover targets a crash left firing, settle working exits
    from the broker, place stops that waited for Freeze to go off, and sell
    wings left open while "leave" is paused.

    Each step takes EXITS_LOCK on its own (per exit in the loops), and its sends stay
    under it, since they are the claim that keeps one closing order per side. Only the
    read of a working exit's order is made without it (_check)."""
    if _frozen(deps):
        return
    for step in (
        _recover_firing,
        _recover_chasing,
        _recover_moving,
        settle_held,
        recover_paired,  # bots slice 21: a paired close a crash left pending
        _recover_placing_targets,  # and a changed futures target cut off mid-send
    ):
        with EXITS_LOCK:
            step(deps)
    with deps.session_factory() as session:
        working = [r.id for r in session.scalars(select(BotExit).where(BotExit.state == "working"))]
        pending = [
            r.id
            for r in session.scalars(
                select(BotExit).where(BotExit.kind == "stop", BotExit.state == "pending")
            )
        ]
        pending_targets = list(
            session.scalars(
                select(BotExit.id).where(BotExit.kind == "target", BotExit.state == "pending")
            )
        )
        left = [
            r.id
            for r in session.scalars(
                select(BotExit).where(BotExit.kind == "wing", BotExit.state == "left")
            )
        ]
        # Bots slice 11: a watched stop past its option's expiry ends with it, as a
        # broker's GTC stop would.
        lapsed = [
            r.id
            for r in session.scalars(
                select(BotExit).where(BotExit.kind == "stop", BotExit.state == WATCHING)
            )
            if stop_expired(r, deps.now())
        ]
    for exit_id in lapsed:
        try:
            with EXITS_LOCK:
                _expire_watched(deps, exit_id)  # it re-reads the row: still watching
        except Exception:  # noqa: BLE001 -- one exit never stops the others
            logger.exception("couldn't expire watched stop %s", exit_id)
    for exit_id in working:
        try:
            _check(deps, exit_id)  # the read without the lock, the write under it
        except Exception:  # noqa: BLE001 -- one exit never stops the others
            logger.exception("couldn't check bot exit %s", exit_id)
    for exit_id in pending:
        try:
            with EXITS_LOCK:
                # A claim may have taken the stop since it was listed: only a stop still
                # pending is placed (rearm itself doesn't look at the state).
                if _still_pending_stop(deps, exit_id):
                    rearm(deps, exit_id, first=True)  # the position check, then the send
        except Exception:  # noqa: BLE001 -- one exit never stops the others
            logger.exception("couldn't place pending bot stop %s", exit_id)
    for exit_id in pending_targets:  # bots slice 17: a futures target Freeze held back
        try:
            with EXITS_LOCK:
                _place_pending_target(deps, exit_id)  # it re-reads the row: still pending
        except Exception:  # noqa: BLE001 -- one exit never stops the others
            logger.exception("couldn't place pending bot target %s", exit_id)
    if left and bot_store.leave_paused(deps.session_factory, deps.now()):
        for exit_id in left:
            try:
                with EXITS_LOCK:
                    _sell_left_wing(deps, exit_id)  # it re-reads the row: still left
            except Exception:  # noqa: BLE001 -- one exit never stops the others
                logger.exception("couldn't sell left wing %s", exit_id)


def _still_pending_stop(deps, exit_id: int) -> bool:
    """The stop row is still "pending" (monitor_pass, under EXITS_LOCK)."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        return row is not None and row.kind == "stop" and row.state == "pending"


def recover_targets(deps) -> None:
    """At startup: settle targets a crash left firing and wings it left chasing."""
    with EXITS_LOCK:
        _recover_firing(deps)
        _recover_chasing(deps)
        _recover_moving(deps)
        _recover_placing_targets(deps)


def _recover_chasing(deps) -> None:
    """A wing left "chasing" with no job working on it (the app died mid-chase,
    close_jobs.recover_stale_closes): watch its last order if it has one, else fail
    it. Either way it alerts. Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        stuck = [
            r.id
            for r in session.scalars(
                select(BotExit).where(BotExit.kind == "wing", BotExit.state == "chasing")
            )
            if r.id not in FIRING
        ]
    for exit_id in stuck:
        try:
            with deps.session_factory() as session:
                row = session.get(BotExit, exit_id)
                bot = _bot_of(session, row)
                if row.broker_order_id:
                    row.state = "working"  # the monitor reads it: filled, or failed with an alert
                    _log(row, deps, event="the chase was cut off by a restart; watching its order")
                    detail = (
                        f"The chase selling {row.long_symbol.strip()} was cut off by a restart. "
                        "The app is watching its last order. Check your broker."
                    )
                else:
                    row.state = "failed"
                    _log(row, deps, event="the chase was cut off by a restart before any order")
                    detail = (
                        f"The chase selling {row.long_symbol.strip()} was cut off by a restart "
                        "before it sent anything; the wing may still be open. Check your broker."
                    )
                session.commit()
                name, bot_id = bot.name, bot.id
            _alert(deps, f"{name}: a wing chase was cut off", detail, bot_id)
        except Exception:  # noqa: BLE001 -- one exit never stops the others
            logger.exception("couldn't recover wing chase %s", exit_id)


def _recover_firing(deps) -> None:
    """A target left "firing" with no fire_target working on it (a crash
    mid-target): its stop was marked cancelled before the cancel went out. Read
    the stop back: put it back to working if it may still be working or has
    filled, and re-place it only if the broker confirms it's gone. Called with
    EXITS_LOCK held."""
    with deps.session_factory() as session:
        stuck = [
            (t.id, t.run_id, t.group_position)
            for t in session.scalars(
                select(BotExit).where(BotExit.kind.in_(CLOSES), BotExit.state == "firing")
            )
            if t.id not in FIRING
        ]
    for exit_id, run_id, position in stuck:
        try:
            with deps.session_factory() as session:
                bot_id = session.get(BotRun, run_id).bot_id
                stop = next(
                    (
                        s
                        for s in session.scalars(
                            select(BotExit)
                            .where(
                                BotExit.run_id == run_id,
                                BotExit.group_position == position,
                                BotExit.kind == "stop",
                                BotExit.state == "cancelled",
                            )
                            .order_by(BotExit.id.desc())
                        )
                        if s.log
                        and s.log[-1].get("event")
                        in (CANCELLED_FOR_TARGET, CANCELLED_FOR_TIMED, CANCELLED_FOR_CLOSE)
                    ),
                    None,
                )
                stop_id = stop.id if stop else None
            # The cancel may or may not have gone out: read the broker either way.
            _recover(deps, exit_id, bot_id, stop_id, True, "left firing by a restart")
        except Exception:  # noqa: BLE001 -- one target never stops the others
            logger.exception("couldn't recover bot target %s", exit_id)


@dataclass(frozen=True)
class _Seen:
    """A working exit's order as the broker showed it, and the row as it was then."""

    exit_id: int
    order_id: str
    log_len: int
    found: BrokerOrder


def _check(deps, exit_id: int) -> None:
    """Read a working exit's order with no lock held, then act on it under EXITS_LOCK
    only if the row hasn't changed meanwhile."""
    seen = _read_working(deps, exit_id)
    if seen is None:
        return
    with EXITS_LOCK:
        _apply_check(deps, seen)


def _read_working(deps, exit_id: int) -> _Seen | None:
    """The broker's order for a "working" exit, or None if the row isn't working. Takes
    no lock, and no DB session is open during the broker call."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        if row is None or row.state != "working":
            return None
        bot = _bot_of(session, row)
        run_row = session.get(BotRun, row.run_id)
        account = run_row.account_number or bot.account_number
        broker = run_row.broker or bot.broker
        order_id, log_len = row.broker_order_id, len(row.log or [])
    found = _client(deps, broker).get_order(account, order_id)
    return _Seen(exit_id, order_id, log_len, found)


def _apply_check(deps, seen: _Seen) -> None:
    """Act on what the broker showed for a working exit. Called with EXITS_LOCK held.
    Changes nothing unless the row is as it was read: still "working", the same order,
    the same log. A claim that landed meanwhile changed it, and the claim's own job
    reads the stop back itself; the next pass reads anything else again."""
    exit_id, found = seen.exit_id, seen.found
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        if (
            row is None
            or row.state != "working"
            or row.broker_order_id != seen.order_id
            or len(row.log or []) != seen.log_len
        ):
            logger.debug("exit %s changed while it was read; the next pass reads it again",
                         exit_id)  # fmt: skip
            return
        bot = _bot_of(session, row)
        run_row = session.get(BotRun, row.run_id)
        if found.status is OrderStatus.FILLED:
            row.state = "filled"
            row.fill_price = next(
                (leg.fill_price for leg in found.legs if leg.fill_price is not None), None
            )
            _log(row, deps, event="filled", price=str(row.fill_price))
            if row.kind == "target":  # a resting target: futures (slice 17) or chosen (0048)
                # One commit with the fill: never a target filled while its stop still watches.
                _end_the_watch(session, deps, row.run_id, row.group_position,
                               "the profit target filled")  # fmt: skip
            # Bots slice 21: the paired close's claim, committed with the fill. A pairing
            # that can't be written never costs the fill (a savepoint): it's said instead.
            paired, paired_broke = None, False
            if row.kind == "stop":
                try:
                    with session.begin_nested():
                        paired = _paired_row(deps, session, row)
                except Exception:  # noqa: BLE001 -- the fill is recorded either way
                    logger.exception("couldn't write the paired close for stop %s", exit_id)
                    paired, paired_broke = None, True
            session.commit()
            paired_id = paired.id if paired is not None else None
            if paired_broke:
                _alert(
                    deps, f"{bot.name}: paired close not started",
                    "A stop filled, but closing the group it pairs with couldn't be started. "
                    "That group keeps its own stop and target. Check your broker.", bot.id,
                    run_id=row.run_id,
                )  # fmt: skip
            if row.kind == "stop":
                _stop_filled(deps, exit_id)
                if paired_id is not None:
                    _paired_close_safely(deps, paired_id)
            elif row.kind == "target":
                name = _group_name(bot_store.load(deps.session_factory, bot.id), row.group_position)
                _alert(
                    deps, f"{bot.name}: profit target filled",
                    f"{name} closed at its {row.limit} limit ({_dollars(run_row, row.limit)} "
                    "a contract).", bot.id, run_id=row.run_id, routine=True,
                )  # fmt: skip
            return
        if found.may_be_working:
            if row.kind == "target" and row.log and row.log[-1].get("event") == CHANGING_TARGET:
                _log(row, deps, event="not changed: the change was cut off; the target still rests")
                session.commit()  # bots slice 21: a stale marker is cleared once read working
            return
        if row.kind == "target":  # bots slice 17: a resting futures target
            loaded = bot_store.load(deps.session_factory, bot.id)
            if not _cut_off_change(deps, row, loaded, found):  # slice 21
                _target_ended(deps, row, loaded, found.status, futures_run=bool(run_row.pt_only))
            session.commit()
        elif row.kind == "stop":
            row.state = "cancelled"
            _log(row, deps, event=f"broker shows {found.status}")
            session.commit()
            rearm(deps, exit_id)
        elif row.kind == "wing" and exit_math.after_expiry(row.long_symbol, deps.now()):
            row.state = "expired"  # it expired with the option: nothing left to sell
            _log(row, deps, event=f"broker shows {found.status} after expiry")
            session.commit()
        else:
            row.state = "failed"
            _log(row, deps, event=f"broker shows {found.status}")
            session.commit()
            if row.kind == "wing":
                _alert(
                    deps,
                    f"{bot.name}: the wing wasn't sold",
                    f"The order selling {row.long_symbol.strip()} ended {found.status}. "
                    "Check your broker.",
                    bot.id,
                )


@dataclass
class _Wing:
    """One wing order, or the choice not to send one, as its BotExit row records it."""

    state: str  # working, filled, failed, cancelled, expired or left
    contracts: int
    broker_order_id: str | None = None
    log: list = field(default_factory=list)
    fill_price: Decimal | None = None
    note: str = ""  # added to the stop-filled alert


def _quote(deps, symbol: str):
    """The symbol's quote if it's fresh, else None (remainder._get_live_bid:
    anything unreadable counts as no quote)."""
    try:
        deps.hub.subscribe({symbol})
        quote = deps.hub.latest(symbol)
    except Exception:  # noqa: BLE001 -- no quote is the safe answer
        logger.exception("couldn't read the quote for %s", symbol)
        return None
    if quote is None or not exit_math.recent(quote.at, deps.now()):
        return None
    return quote


def _bid(deps, symbol: str) -> Decimal | None:
    quote = _quote(deps, symbol)
    return quote.bid if quote is not None and quote.bid is not None and quote.bid > 0 else None


def _mid(deps, symbol: str) -> Decimal | None:
    quote = _quote(deps, symbol)
    if quote is None or quote.bid is None or quote.ask is None:
        return None
    if quote.bid <= 0 or quote.ask < quote.bid:
        return None
    return (quote.bid + quote.ask) / 2


def _broker_refused(result) -> bool:
    """Nothing is out at the broker: the broker turned it down, its pre-send check
    failed, or credentials were missing (action "send", not ok, not unclear).
    Never a central-check refusal (final), a preview, or an unclear send (it may
    be live)."""
    return (
        result.action == "send" and not result.ok and not result.message.startswith(orders.UNCLEAR)
    )


def _resting_tif(deps, symbol: str, order_type: OrderType) -> TimeInForce:
    """A resting limit sale of a leg that doesn't expire today is GTC, so it keeps working
    overnight (bots slice 14, question 7; Tracker2's remainder rests it DAY). A market
    order, or a leg expiring today, is DAY as before."""
    parsed = parse_occ(symbol)
    today = deps.now().astimezone(MARKET_TZ).date()
    if order_type is OrderType.LIMIT and parsed is not None and parsed[1].expiration > today:
        return TimeInForce.GTC
    return TimeInForce.DAY


def _sell(deps, bot, run: _Run, long: str, size: int, order_type=OrderType.LIMIT, price=None):
    order = Order(
        (Leg(long, Side.SELL),), size, False, order_type, price=price,
        time_in_force=_resting_tif(deps, long, order_type),
    )  # fmt: skip
    return orders.submit(
        deps.session_factory, deps.clients(), _request(bot, run, order),
        now=deps.now(), notify=deps.notify,
    )  # fmt: skip


def _one_tick(deps, bot, run, long, size, log) -> _Wing:
    """remainder.close_long_leg's fallback: a sell resting at one tick. It can't
    fill badly: a marketable sell limit takes the bid."""
    price = exit_math.wing_limit(long, Decimal(0))
    result = _sell(deps, bot, run, long, size, price=price)
    log.append({"at": deps.now().isoformat(), "one_tick": f"{price:f}", "result": result.message})
    if _placed(result):
        return _Wing("working", size, result.broker_order_id, log)
    return _Wing("failed", size, None, log)


def _sell_now(deps, bot, run, long, size, log) -> _Wing:
    """close_market (remainder.close_long_leg): at market only with a fresh bid;
    with none, or if the broker turns the market order down, a one-tick limit."""
    bid = _bid(deps, long)
    if bid is None:
        log.append({"at": deps.now().isoformat(), "event": "no live bid; a one-tick limit"})
        return _one_tick(deps, bot, run, long, size, log)
    result = _sell(deps, bot, run, long, size, OrderType.MARKET)
    log.append({"at": deps.now().isoformat(), "market": f"bid {bid:f}", "result": result.message})
    if _placed(result):
        return _Wing("working", size, result.broker_order_id, log)
    if _broker_refused(result):
        return _one_tick(deps, bot, run, long, size, log)
    return _Wing("failed", size, None, log)


def _sell_at_limit(deps, bot, run, long, size, limit, log) -> _Wing:
    """close_limit: rest at the side's price, or the live bid, on tick (rounded
    down, never below one tick). With neither, or turned down, one tick."""
    at = limit if limit is not None else _bid(deps, long)
    if at is None or at <= 0:
        log.append({"at": deps.now().isoformat(), "event": "no price and no live bid; one tick"})
        return _one_tick(deps, bot, run, long, size, log)
    price = exit_math.wing_limit(long, at)
    result = _sell(deps, bot, run, long, size, price=price)
    log.append({"at": deps.now().isoformat(), "limit": f"{price:f}", "result": result.message})
    if _placed(result):
        return _Wing("working", size, result.broker_order_id, log)
    if _broker_refused(result):
        return _one_tick(deps, bot, run, long, size, log)
    return _Wing("failed", size, None, log)


def _chase_wing(deps, bot, run, long, size, log, on_order=None, in_session=False) -> list[_Wing]:
    """close_chase (close_jobs.execute_close with the exit chase): a limit sell
    from the mid, one step lower each interval. What it doesn't sell is sold now
    (a single leg may go to market after its last try). A chase whose first
    send the broker turns down has nothing out, so the wing is sold now
    (remainder.py's close_chase fallback). After an unclear send, a
    central-check refusal (final) or a preview, nothing more is sent.
    `in_session` (a leg-out's wing): never at market once the regular session
    is over; what's left rests at one tick instead."""

    def sell_now(size, log):
        if in_session and not _session_open(deps):
            log.append({"at": deps.now().isoformat(), "event": "the session is over; one tick"})
            return _one_tick(deps, bot, run, long, size, log)
        return _sell_now(deps, bot, run, long, size, log)

    mid = _mid(deps, long)
    if mid is None:
        log.append({"at": deps.now().isoformat(), "event": "no live quote to chase from"})
        return [sell_now(size, log)]
    first = exit_math.wing_limit(long, mid - bot.exit_chase.start_offset)

    probe = Order((Leg(long, Side.SELL),), 1, False, OrderType.MARKET)

    def lower(price, step):
        step = max(step, ticks.order_tick(probe, price, {}))  # execution._concede
        following = exit_math.wing_limit(long, price - step)
        return following if following < price else None

    order = Order((Leg(long, Side.SELL),), size, False, price=first)
    outcome = chase(deps, _request(bot, run, order), first, bot.exit_chase, lower, on_order)
    order_id = outcome.order.broker_order_id if outcome.order else None
    log.append({"at": deps.now().isoformat(), "chase": outcome.log, "status": outcome.status})
    if outcome.status == "filled":
        fill = next((leg.fill_price for leg in outcome.order.legs if leg.fill_price), None)
        return [_Wing("filled", size, order_id, log, fill)]
    if outcome.status in ("missed", "partial"):
        legs = outcome.order.legs if outcome.order else ()
        sold = sum(leg.quantity - leg.remaining for leg in legs)
        log.append({"event": f"the chase sold {sold} of {size}; selling the rest now"})
        rest = size - sold
        chased = _Wing("cancelled", sold, order_id, log)
        if rest <= 0:
            return [chased]
        return [chased, sell_now(rest, [])]
    if outcome.status == "unclear" and outcome.order is not None:
        # Its cancel wasn't confirmed: the monitor keeps reading it.
        return [
            _Wing(
                "working",
                size,
                order_id,
                log,
                note=" The chase's last order may still be working. Check your broker.",
            )
        ]
    if outcome.status == "refused" and outcome.broker_refused:
        log.append(
            {"at": deps.now().isoformat(), "event": "the broker turned the chase down; selling now"}
        )
        return [sell_now(size, log)]
    return [_Wing("failed", size, order_id, log, note=f" ({outcome.message or outcome.status})")]


def _wing_cols(anchor: str | None, leg: exit_math.GroupLeg) -> dict:
    """A wing row's columns: the one leg it sells (`legs`, `long_symbol`, its contracts
    in `contracts`) and the group's stop leg (`anchor_symbol`; `short_symbol` is the
    anchor or "")."""
    return dict(
        short_symbol=anchor or "", long_symbol=leg.symbol, anchor_symbol=anchor,
        legs=[{"symbol": leg.symbol, "side": leg.side.value, "ratio": leg.ratio}],
    )  # fmt: skip


def _wing_leg(row: BotExit) -> tuple[str | None, exit_math.GroupLeg]:
    """A wing row's stop leg (None for a group with none) and the one leg it sells. A
    row with no `legs` (written before slice 13) sells its `long_symbol` after the stop
    on its `short_symbol`."""
    if row.legs:
        return row.anchor_symbol, exit_legs(row)[0]
    return row.short_symbol or None, exit_math.GroupLeg(row.long_symbol, Side.BUY, 1)


def _record_wings(deps, run_id, position, anchor, leg, wings: list[_Wing]) -> None:
    now = deps.now()
    with deps.session_factory() as session:
        for wing in wings:
            session.add(BotExit(
                run_id=run_id, group_position=position, kind="wing", state=wing.state,
                contracts=wing.contracts, broker_order_id=wing.broker_order_id,
                fill_price=wing.fill_price, rearms=0, log=wing.log, created_at=now,
                updated_at=now, **_wing_cols(anchor, leg),
            ))  # fmt: skip
        session.commit()


# How each of the group's other legs ended after its stop, for the stop-filled alert;
# any other state: NOT_SOLD.
NOT_SOLD = "wasn't sold"
_LEG_SAYS = {
    "working": "is being sold",
    "filled": "was sold",
    "expired": "is no longer held",
    "left": "is left open, as the group is set to",
}


def _other_legs(stop: BotExit) -> list[exit_math.GroupLeg]:
    """What a resting stop leaves of its group when it fills, in leg order: every leg
    but the anchor (remainder._open_legs_of_group). Only bought legs: a single_short
    group has no other sold leg, and the remainder never sells a sold leg again. Empty
    for a naked short."""
    anchor = plain(stop.anchor_symbol or stop.short_symbol)
    return [
        leg
        for leg in exit_legs(stop)
        if leg.symbol and plain(leg.symbol) != anchor and leg.side is Side.BUY
    ]


def _rest_unhandled(deps, name: str, bot_id, symbols: list[str]) -> None:
    """The alert when the rest of a group after its stop wasn't handled: one other leg is
    still "the wing" (slices 1-12), more are "the group's other legs"."""
    if not symbols:  # a naked short: nothing else to sell
        title, detail = "it wasn't handled", "The stop filled; check your broker."
    elif len(symbols) == 1:
        title = "the wing wasn't handled"
        detail = f"Check your broker: {symbols[0].strip()} may still be open."
    else:
        title = "the group's other legs weren't handled"
        detail = f"Check your broker: {_listed(symbols)} may still be open."
    _alert(deps, f"{name}: a stop filled but {title}", detail, bot_id)


def _stop_filled(deps, stop_id: int) -> None:
    """remainder.handle_stop_fill: cancel the group's target, then close every other leg
    of the group as it says (on_stop_remainder): sold now, at a limit, chased, or left
    open (unless "leave" is paused). Only the group's bought legs are touched, each on
    its own order and wing row, each sized to what's held and never more than this
    run's units x its ratio. A naked short has nothing else to sell. The stop is
    already recorded filled, so anything that breaks says so."""
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        bot_row = _bot_of(session, stop)
        name, bot_id = bot_row.name, bot_row.id
        others = [leg.symbol for leg in _other_legs(stop)]
    try:
        _handle_stop_fill(deps, stop_id)
    except Exception:  # noqa: BLE001 -- the stop filled: never go quiet about its group
        logger.exception("couldn't handle the rest of the group after stop %s filled", stop_id)
        _rest_unhandled(deps, name, bot_id, others)


def _handle_stop_fill(deps, stop_id: int) -> None:
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        _cancel_side_target(session, deps, stop, "the side's stop filled")
        others = _other_legs(stop)
        if not others:
            _log(stop, deps, event="the group has no other legs: closed with its stop")
        session.commit()
        run_id, position, units = stop.run_id, stop.group_position, stop.contracts
        anchor = stop.anchor_symbol or stop.short_symbol
    run, bot = _run_and_bot(deps, run_id)
    if not others:  # a naked short: its stop closed the group
        _alert(
            deps, f"{bot.name}: stop filled",
            f"The stop on {anchor.strip()} filled; the group had no other legs, so it's closed.",
            bot.id,
        )  # fmt: skip
        return
    with deps.session_factory() as session:  # slice 21: this trade's own wing rule first
        group = effective_group(session, run_id, bot, position)
    configured = group.on_stop_remainder if group is not None else "close_market"
    paused = configured == "leave" and bot_store.leave_paused(deps.session_factory, deps.now())
    mode = exit_math.remainder_mode(configured, paused)
    # Sell what's actually open of each leg, never more than this run's units of it (the
    # stop's units are already less what profit actions closed): another run may hold
    # the same leg (remainder.run_leg_contracts).
    try:
        held = _positions(deps, run)
        sizes = [min(max(held.get(plain(leg.symbol), 0), 0), units * leg.ratio) for leg in others]
    except Exception:  # noqa: BLE001 -- unreadable: try the full size; the order check decides
        logger.exception("couldn't read positions before selling the rest of a group")
        sizes = [units * leg.ratio for leg in others]
    log = [{"at": deps.now().isoformat(), "mode": mode, "leave_paused": paused}]
    sized = list(zip(others, sizes, strict=True))
    limit = group.remainder_limit if group is not None else None
    if mode == "close_limit" and limit is not None and len(others) > 1:
        # A typed limit is one price for one leg (validation refuses it now); a bot saved
        # before that sells each leg at its own bid instead.
        limit = None
        log.append({"event": f"one limit price can't fit {len(others)} legs; each leg's bid"})
    if mode == "close_chase" and any(sizes):
        # The chases run on their own thread (close_jobs.enqueue), one leg at a time;
        # each leg's row says "chasing" until its chase ends.
        _start_wing_chase(deps, run_id, position, anchor, sized, log, paused)
        return
    said = []
    for leg, size in sized:  # remainder._close_legs_smart: one order per leg, in leg order
        own = list(log)
        if size == 0:
            wings = [_Wing("expired", 0, log=own + [{"event": "the leg isn't held"}])]
        elif mode == "leave":
            wings = [
                _Wing("left", size, log=own + [{"event": "left open, as the group is set to"}])
            ]
        elif mode == "close_limit":
            wings = [_sell_at_limit(deps, bot, run, leg.symbol, size, limit, own)]
        else:
            wings = [_sell_now(deps, bot, run, leg.symbol, size, own)]
        _record_wings(deps, run_id, position, anchor, leg, wings)
        said.append((leg.symbol, wings))
    _stop_alert(deps, bot, anchor, said, paused)


# --- Bots slice 21: closing another group after a stop (remainder._handle_paired_close) ----

PAIRED = "paired"  # bot_exits.kind of a paired close's row, on the group it closes
CLAIMING = "claiming its close"  # logged before fire_close: recovery looks for that close
_PAIRED_BROKE: set[int] = set()  # paired rows whose step broke: alerted once


def _paired_row(deps, session, stop: BotExit) -> BotExit | None:
    """When a resting stop fills and its group closes another (on_stop_close_group_id),
    the other group's "paired" row, "pending", added to `session` to commit with the fill:
    the claim of this step, so a crash before the close is claimed leaves a row the monitor
    picks up. None when its group pairs nothing (or the trade doesn't have that group)."""
    run = session.get(BotRun, stop.run_id)
    bot = bot_store.load(deps.session_factory, run.bot_id)
    groups = {g.position: g for g in bot.groups} if bot is not None else {}
    group = groups.get(stop.group_position)
    target = group.close_on_stop if group is not None else None
    if target is None or target == stop.group_position:
        return None
    if not any(leg.get("group") == target for leg in run.legs or ()):
        _log(stop, deps, event=f"its pairing names group {target + 1}, not in this trade")
        return None
    rows = list(session.scalars(select(BotExit).where(
        BotExit.run_id == run.id, BotExit.group_position == target)))  # fmt: skip
    now = deps.now()
    row = BotExit(
        run_id=run.id, group_position=target, kind=PAIRED, state="pending",
        **group_columns(session, run, target, rows), contracts=group_units(session, run, target),
        rearms=0, created_at=now, updated_at=now,
        log=[{"at": now.isoformat(), "event": f"after {group.name}'s stop filled",
              "from": stop.group_position, "stop": stop.id, "by": f"{group.name}'s stop"}],
    )  # fmt: skip
    session.add(row)
    session.flush()
    return row


def _paired_close_safely(deps, row_id: int) -> None:
    """_paired_close, never raising: a step that breaks is alerted once and left "pending"
    for the next monitor pass (nothing was claimed, or the claim's close is found then)."""
    try:
        _paired_close(deps, row_id)
    except Exception:  # noqa: BLE001 -- the stop filled: never go quiet about the other group
        logger.exception("the paired close %s broke", row_id)
        if row_id in _PAIRED_BROKE:
            return
        _PAIRED_BROKE.add(row_id)
        with deps.session_factory() as session:
            row = session.get(BotExit, row_id)
            bot = _bot_of(session, row)
            run_id, name, bot_id = row.run_id, bot.name, bot.id
            group = _group_name(bot_store.load(deps.session_factory, bot_id), row.group_position)
        _alert(
            deps, f"{name}: a paired close broke",
            f"A stop filled and {group} should be closed after it, but closing it went wrong. "
            "The app tries again at its next check. Check your broker.", bot_id, run_id=run_id,
        )  # fmt: skip


def _finish_pair(deps, row_id: int, state: str, event: str) -> None:
    with deps.session_factory() as session:
        row = session.get(BotExit, row_id)
        row.state = state
        _log(row, deps, event=event)
        session.commit()


def _claimed_close(session, row: BotExit, by: str) -> BotExit | None:
    """The close a cut-off paired step's claim made: a close row on that group, started
    after the paired row, by the same stop."""
    found = [
        r for r in session.scalars(select(BotExit).where(
            BotExit.run_id == row.run_id, BotExit.group_position == row.group_position,
            BotExit.kind == "close", BotExit.created_at >= row.created_at))
        if _by_of(r.log) == by
    ]  # fmt: skip
    return max(found, key=lambda r: r.id, default=None)


def _paired_close(deps, row_id: int) -> None:
    """Close the paired group because the stop that pairs it just filled
    (remainder._handle_paired_close). The pairing sets the timing; the group's own wing
    rule the manner (this trade's, else the bot's, _effective_remainder_mode): leave
    (while not paused) sends nothing; otherwise slice 6's close of its open units, claimed
    under the lock like every close (its stop and target pulled, the cancel confirmed,
    sized to what the broker holds), at market on a one-leg group set to sell now, else by
    the exit chase. Outside the regular session, held back or already closing: nothing is
    sent, alerted once, never tried again (question 8). Freeze: it waits, pending. Called
    from the monitor, under EXITS_LOCK."""
    with EXITS_LOCK:
        with deps.session_factory() as session:
            row = session.get(BotExit, row_id)
            if row is None or row.state != "pending":
                return
            run_row = session.get(BotRun, row.run_id)
            run_id, target, by = row.run_id, row.group_position, row.log[0].get("by", "a stop")
            claiming = any(e.get("event") == CLAIMING for e in row.log)
            created = row.created_at
            rows = [
                r for r in session.scalars(select(BotExit).where(
                    BotExit.run_id == run_id, BotExit.group_position == target))
                if r.id != row_id
            ]  # fmt: skip
            state = timing.side_state((r.kind, r.state) for r in rows)
            claimed = _claimed_close(session, row, by) if claiming else None
            units = group_units(session, run_row, target)
            legs, _ = exit_math.group_legs(run_row.legs, target)
        if claimed is not None:  # cut off after its claim: finish on that close
            _finish_pair(deps, row_id, "filled", f"closing (close {claimed.id})")
            return
        run, bot = _run_and_bot(deps, run_id)
        name = _group_name(bot, target)
        today = deps.now().astimezone(MARKET_TZ).date()
        if created.astimezone(MARKET_TZ).date() < today:
            # Held past its session day (Freeze, or the app down): never sent a day late.
            _finish_pair(deps, row_id, "expired", "not closed: its session day is over")
            _alert(
                deps, f"{bot.name}: paired close not done",
                f"{by[:1].upper()}{by[1:]} filled, but {name} wasn't closed after it before "
                "that session day ended (orders were frozen, or the app wasn't running). It "
                "keeps its own stop and target. Check your broker.", bot.id, run_id=run_id,
            )  # fmt: skip
            return
        if _frozen(deps):
            return  # pending: the monitor places it once Freeze is off
        with deps.session_factory() as session:
            group = effective_group(session, run_id, bot, target)
        if state == "closed" or units <= 0:
            return _finish_pair(deps, row_id, "expired", "nothing of it is open")

        def not_closed(why: str) -> None:
            _finish_pair(deps, row_id, "failed", f"not closed: {why}")
            _alert(
                deps, f"{bot.name}: paired close not done",
                f"{by[:1].upper()}{by[1:]} filled, but {name} wasn't closed after it: {why}. "
                "It keeps its own stop and target; it won't be tried again. Check your broker.",
                bot.id, run_id=run_id,
            )  # fmt: skip

        if not _session_open(deps):
            return not_closed("the regular session is over")
        if held_back(rows):
            return not_closed("an earlier close or stop change on it may still be working")
        if state == "busy":
            return not_closed("a close is already running on it")
        configured = group.on_stop_remainder if group is not None else "close_market"
        paused = configured == "leave" and bot_store.leave_paused(deps.session_factory, deps.now())
        mode = exit_math.remainder_mode(configured, paused)
        if mode == "leave":
            _finish_pair(deps, row_id, "left", "left open, as the group is set to")
            _alert(deps, f"{bot.name}: {by} filled",
                   f"{name} is left open after it, as the group is set to.", bot.id,
                   run_id=run_id, routine=True)  # fmt: skip
            return
        market = mode == "close_market" and len(legs) == 1
        # "Sold at a limit" with a typed price on a one-leg bought group: the chase never
        # asks less than that price (remainder close_limit's own price)
        floor = None
        if (mode == "close_limit" and group is not None and group.remainder_limit is not None
                and len(legs) == 1 and legs[0].side is Side.BUY):  # fmt: skip
            floor = group.remainder_limit
        with deps.session_factory() as session:
            _log(session.get(BotExit, row_id), deps, event=CLAIMING)
            session.commit()
        close_id = fire_close(deps, run_id, target, units, units, by=by,
                              order_type=MARKET if market else None, floor=floor)  # fmt: skip
        if close_id is None:
            return not_closed("it couldn't be claimed (a close is running on it, or it closed)")
        _finish_pair(deps, row_id, "filled", f"closing {units} (close {close_id})")
        how = "at market" if market else "from the mid, by the exit chase"
        _alert(deps, f"{bot.name}: {by} filled",
               f"After it, closing {name} too ({units}, {how}): its stop and target came off "
               "first.", bot.id, run_id=run_id)  # fmt: skip


def recover_paired(deps) -> None:
    """Each monitor pass: a paired close still "pending" (a crash between the fill and the
    claim, or Freeze) goes on from where it stopped. Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        pending = list(session.scalars(select(BotExit.id).where(
            BotExit.kind == PAIRED, BotExit.state == "pending")))  # fmt: skip
    for row_id in pending:
        _paired_close_safely(deps, row_id)


def _new_wing_row(deps, run_id, position, anchor, leg, size, log) -> int:
    """A wing's row, written "chasing" before its chase sends anything. It is in
    FIRING before the row is committed, so recovery never sees it "chasing" with
    no job (the caller discards it from FIRING when its chase ends)."""
    now = deps.now()
    with deps.session_factory() as session:
        row = BotExit(
            run_id=run_id, group_position=position, kind="wing", state="chasing",
            contracts=size, rearms=0, log=log, created_at=now, updated_at=now,
            **_wing_cols(anchor, leg),
        )  # fmt: skip
        session.add(row)
        session.flush()
        FIRING.add(row.id)
        try:
            session.commit()
        except BaseException:
            FIRING.discard(row.id)
            raise
        return row.id


def _run_wing_chase(deps, wing_id, bot, run, long, size, log, in_session=False) -> list[_Wing]:
    """Chase the wing on its "chasing" row, recording each order as it goes, then
    write the outcome (the first result on the row, any more on rows of their own)."""
    wings = _chase_wing(
        deps, bot, run, long, size, log, on_order=lambda oid: _note_order(deps, wing_id, oid),
        in_session=in_session,
    )  # fmt: skip
    _finish_wing_chase(deps, wing_id, wings)
    return wings


def _start_wing_chase(deps, run_id, position, anchor, sized, log, paused) -> None:
    """Write each held leg's row as "chasing" (a leg not held: "expired"), then chase
    them one at a time in the background. `plan` keeps the legs in order: each one's
    outcome, or the id of its row to chase."""
    plan, ids = [], []
    try:
        for leg, size in sized:
            if size == 0:
                wings = [_Wing("expired", 0, log=list(log) + [{"event": "the leg isn't held"}])]
                _record_wings(deps, run_id, position, anchor, leg, wings)
                plan.append((leg.symbol, wings))
            else:
                wing_id = _new_wing_row(deps, run_id, position, anchor, leg, size, list(log))
                ids.append(wing_id)  # in FIRING
                plan.append((leg.symbol, wing_id))
    except BaseException:
        FIRING.difference_update(ids)  # recovery fails them, with an alert
        raise
    try:
        deps.background(_wing_job, deps, plan, paused)
    except Exception:  # noqa: BLE001 -- no thread: the legs are sold now instead
        logger.exception("couldn't start the chase for wings %s", ids)
        FIRING.difference_update(ids)
        run, bot = _run_and_bot(deps, run_id)
        done = []
        for symbol, item in plan:
            if isinstance(item, list):
                done.append((symbol, item))
                continue
            with deps.session_factory() as session:
                row = session.get(BotExit, item)
                size, own = row.contracts, list(row.log)
            own.append(
                {"at": deps.now().isoformat(), "event": "couldn't start the chase; selling now"}
            )
            wing = _sell_now(deps, bot, run, symbol, size, own)
            _finish_wing_chase(deps, item, [wing])
            done.append((symbol, [wing]))
        _stop_alert(deps, bot, anchor, done, paused)


def _wing_job(deps, plan: list, paused: bool) -> None:
    """The background chase of a group's other legs after its stop (remainder
    close_chase), one leg at a time, then one alert. Never raises."""
    ids = [item for _, item in plan if not isinstance(item, list)]
    try:
        with deps.session_factory() as session:
            first = session.get(BotExit, ids[0])
            run_id, (anchor, _) = first.run_id, _wing_leg(first)
        run, bot = _run_and_bot(deps, run_id)
        done = []
        for symbol, item in plan:
            if isinstance(item, list):
                done.append((symbol, item))
                continue
            with deps.session_factory() as session:
                row = session.get(BotExit, item)
                size, log = row.contracts, list(row.log)
            done.append((symbol, _run_wing_chase(deps, item, bot, run, symbol, size, log)))
        _stop_alert(deps, bot, anchor, done, paused)
    except Exception:  # noqa: BLE001 -- the stop filled: never go quiet about its group
        logger.exception("the chase for wings %s failed part-way", ids)
        _wing_job_broke(deps, ids)
    finally:
        FIRING.difference_update(ids)


def _note_order(deps, exit_id: int, order_id: str) -> None:
    """A chase's current order, on its row before any wait (on_order_id)."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        row.broker_order_id = order_id
        row.updated_at = deps.now()
        session.commit()


def _finish_wing_chase(deps, wing_id: int, wings: list[_Wing]) -> None:
    """The chase's first result goes on its "chasing" row; any more get rows of their own."""
    first, rest = wings[0], wings[1:]
    with deps.session_factory() as session:
        row = session.get(BotExit, wing_id)
        row.state, row.contracts, row.fill_price = first.state, first.contracts, first.fill_price
        row.broker_order_id = first.broker_order_id or row.broker_order_id
        row.log, row.updated_at = first.log, deps.now()
        session.commit()
        run_id, position, (anchor, leg) = row.run_id, row.group_position, _wing_leg(row)
    if rest:
        _record_wings(deps, run_id, position, anchor, leg, rest)


def _wing_job_broke(deps, wing_ids: list[int]) -> None:
    """Watch each broken chase's last order if there is one (it may be live), else fail
    its row; one alert names the legs whose chases broke."""
    try:
        with deps.session_factory() as session:
            rows = [session.get(BotExit, wing_id) for wing_id in wing_ids]
            broke = []
            for row in rows:
                if row.state == "chasing":
                    row.state = "working" if row.broker_order_id else "failed"
                    _log(row, deps, event="the chase broke part-way")
                    broke.append(row.long_symbol)
            session.commit()
            bot = _bot_of(session, rows[0])
            name, bot_id = bot.name, bot.id
            symbols = broke or [row.long_symbol for row in rows]
    except Exception:  # noqa: BLE001 -- the alert below still goes out
        logger.exception("couldn't settle wings %s", wing_ids)
        name, bot_id, symbols = "A bot", None, []
    _rest_unhandled(deps, name, bot_id, symbols)


def _stop_alert(deps, bot, anchor: str, said: list, paused: bool) -> None:
    """The stop-filled alert: how each of the group's other legs ended (`said`: each
    leg's symbol and its wings, in leg order). One other leg is named on its own."""
    lasts = [(symbol, wings[-1]) for symbol, wings in said]
    states = [last.state for _, last in lasts]

    def says(symbol, last):
        return f"{symbol.strip()} {_LEG_SAYS.get(last.state, NOT_SOLD)}"

    if len(lasts) == 1:
        detail = f"The stop on {anchor.strip()} filled; {says(*lasts[0])}."
    else:
        listed = ", ".join(says(symbol, last) for symbol, last in lasts)
        detail = f"The stop on {anchor.strip()} filled; the group's other legs: {listed}."
    if "left" in states:
        detail += (
            f" Close {'it' if states.count('left') == 1 else 'them'} yourself when you're ready."
        )
    if any(state not in _LEG_SAYS for state in states):
        detail += " Check your broker."
    one = len(lasts) == 1
    if paused and any(state in ("working", "filled") for state in states):
        legs = "the other leg is" if one else "the other legs are"
        detail += f' "Leave" is paused, so {legs} being sold.'
    elif paused and any(state != "expired" for state in states):
        detail += f' "Leave" is paused, so the app tried to sell {"it" if one else "them"}.'
    notes = "".join(last.note for _, last in lasts)
    _alert(deps, f"{bot.name}: stop filled", detail + notes, bot.id)


def _sell_left_wing(deps, exit_id: int) -> None:
    """exit_monitor.check_lapsed_remainders: "leave" is paused, so a leg left open
    after its stop is sold now. The left row is closed first, so a leg is never sold
    twice; if positions can't be read, it's put back for the next pass."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        if row.state != "left":
            return
        bot_row = _bot_of(session, row)
        name, bot_id = bot_row.name, bot_row.id
        row.state = "cancelled"
        _log(row, deps, event="leave is paused; selling the wing")
        session.commit()
        run_id, position, contracts = row.run_id, row.group_position, row.contracts
        anchor, leg = _wing_leg(row)
    try:
        _sell_left(deps, exit_id, run_id, position, anchor, leg, contracts)
    except Exception:  # noqa: BLE001 -- the row stays closed: an order may be out
        logger.exception("selling left wing %s failed part-way", exit_id)
        _alert(
            deps,
            f"{name}: a wing left open wasn't sold",
            f"Selling {leg.symbol.strip()} failed part-way. Check your broker.",
            bot_id,
        )


def _sell_left(deps, exit_id, run_id, position, anchor, leg, contracts) -> None:
    run, bot = _run_and_bot(deps, run_id)
    long = leg.symbol
    if exit_math.after_expiry(long, deps.now()):
        expired = _Wing("expired", 0, log=[{"event": "the option expired"}])
        _record_wings(deps, run_id, position, anchor, leg, [expired])
        return
    try:
        held = _positions(deps, run)
    except Exception:  # noqa: BLE001 -- try again next pass; never guess a size
        logger.exception("couldn't read positions before selling a left wing")
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            row.state = "left"
            _log(row, deps, event="couldn't read positions; trying again next pass")
            session.commit()
        return
    # The row's contracts are this leg's, this run's: never more than that is sold.
    size = min(max(held.get(plain(long), 0), 0), contracts)
    log = [{"at": deps.now().isoformat(), "event": "leave is paused"}]
    wing = _Wing("expired", 0, log=log) if size == 0 else _sell_now(deps, bot, run, long, size, log)
    _record_wings(deps, run_id, position, anchor, leg, [wing])
    if not size:
        return
    if wing.state in ("working", "filled"):
        _alert(
            deps,
            f"{bot.name}: selling a wing left open",
            f'"Leave" is paused, so the wing {long.strip()}, left open after its stop, is '
            "being sold.",
            bot.id,
            run_id=run_id,
        )
        return
    said = next((e["result"] for e in reversed(wing.log) if "result" in e), wing.state)
    _alert(
        deps,
        f"{bot.name}: a wing left open wasn't sold",
        f'"Leave" is paused, but {long.strip()} couldn\'t be sold ({said}). Check your broker.',
        bot.id,
        run_id=run_id,
    )


def _cancel_side_target(session, deps, stop: BotExit, why: str) -> None:
    for target in session.scalars(
        select(BotExit).where(
            BotExit.run_id == stop.run_id,
            BotExit.group_position == stop.group_position,
            BotExit.kind == "target",
            BotExit.state == "armed",
        )
    ):
        target.state = "cancelled"
        _log(target, deps, event=why)


def stop_expired(row: BotExit, now) -> bool:
    """A stop's options are past expiry (the broker expired its GTC stop with them): its
    anchor's, or for a group the app watches on its P&L, any leg's. Its P&L can't be read
    whole once one leg has gone (bots slice 14: a calendar's near leg); for a group in
    one expiration that's the same moment as every leg. Far legs still held are then
    left to you (services/bot_settle.py records them and alerts)."""
    anchor = None if row.watched else (row.anchor_symbol or row.short_symbol or None)
    return exit_math.stop_past_expiry(anchor, (leg.symbol for leg in exit_legs(row)), now)


def _stop_name(row: BotExit) -> str:
    """What a stop is on, for alerts: its anchor, or a watched group's legs."""
    anchor = row.anchor_symbol or row.short_symbol
    return anchor.strip() if anchor else _names(exit_legs(row))


def _anchor_leg(row: BotExit) -> exit_math.GroupLeg:
    """A resting stop's leg, with its ratio (1 for a row from before slice 13)."""
    anchor = row.anchor_symbol or row.short_symbol
    return next(
        (leg for leg in exit_legs(row) if plain(leg.symbol) == plain(anchor)),
        exit_math.GroupLeg(anchor, Side.SELL, 1),
    )


def rearm(deps, exit_id: int, first: bool = False) -> bool:
    """rearm.rearm_trade: re-place a missing stop, at most 3 a side a day, sized
    to what's open. Returns True if the stop ended up working. `first` places a
    stop that waited for Freeze (it isn't a re-arm and doesn't count). Called
    with EXITS_LOCK held; it never takes the lock.

    A resting stop goes back on its anchor only while the anchor is held short: flat or
    long, there's nothing to protect and no buy stop is sent. It's sized units x the
    anchor's ratio (exits._place_stop_loss), the units being the whole units held
    short, a part unit counting as one. With a part unit held (say 5 of a short sold 2
    to a unit) it sends min(units x ratio, held short) = 5, never more than is held
    short: the safe direction, as a stop that bought more would open a long.

    A group the app watches on its P&L (slice 13) is only watched again, never sent: it
    isn't a re-arm, so it never counts toward or stops at the 3 a day (its limit is the
    3 fires a day without a close, decision 0037); it's watched while this run still
    holds any of its legs (_watch_group)."""
    now = deps.now()
    today = now.astimezone(MARKET_TZ).date()
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        run_id, bot_id = row.run_id, session.get(BotRun, row.run_id).bot_id
        if stop_expired(row, now):
            # Expiry evening: the broker expired the stop with the option. Not an alarm.
            row.state = "expired"
            _log(row, deps, event="the option expired; nothing to re-place")
            _cancel_side_target(session, deps, row, "the option expired")
            session.commit()
            return False
        if stop_off(session, row.run_id, row.group_position):
            row.state = "cancelled"  # you switched this side's stop off: nothing is sent
            both = exits_off(session, row.run_id, row.group_position)
            what = "exits are off" if both else "the stop is off"
            _log(row, deps, event=f"{what} for this trade; not placed")
            session.commit()
            return False
        if row.rearm_day != today:
            row.rearm_day, row.rearms = today, 0
        if not first and not row.watched and row.rearms >= exit_math.REARMS_PER_DAY:
            row.state = "failed"
            _log(row, deps, event="re-arm limit reached")
            session.commit()
            name = session.get(Bot, bot_id).name
            if row.app_held:
                _alert(
                    deps,
                    f"{name}: stop no longer watched",
                    f"The app has started watching the stop on {_stop_name(row)} "
                    "again 3 times today, so it couldn't resume watching it: that side has "
                    "no stop. Close it yourself or set a new stop from the run page.",
                    bot_id,
                )
                return False
            _alert(
                deps,
                f"{name}: stop not re-placed",
                f"The stop on {_stop_name(row)} was re-placed 3 times today; it's left to you.",
                bot_id,
            )
            return False
        anchor, contracts, trigger = _anchor_leg(row), row.contracts, row.trigger
        short = anchor.symbol
        group_watched = bool(row.watched)
        app_held = bool(row.app_held)
        session.commit()
    if group_watched:
        # A group the app watches on its P&L has no short to rest on: it's watched again
        # and nothing is ever sent for it.
        return _watch_group(deps, exit_id)
    run, bot = _run_and_bot(deps, run_id)
    held = _held_short(deps, run, short, contracts * anchor.ratio)  # anchor contracts, short
    # Whole units held; a part unit still left short counts, so it's never unprotected.
    units = None if held is None else min(-(-held // anchor.ratio), contracts)
    if app_held and held != 0:
        return _watch_stop(deps, exit_id, first, units)
    if held is None:
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            row.state = "failed"  # the target stays armed: we don't know the side is gone
            _log(row, deps, event="couldn't read positions; stop not re-placed")
            session.commit()
        _alert(
            deps,
            f"{bot.name}: stop not re-placed",
            f"Couldn't read positions, so the stop on {short.strip()} wasn't re-placed. "
            "Check your broker.",
            bot_id,
        )
        return False
    if held == 0:
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            row.state = "expired"
            _log(row, deps, event="position gone; nothing to protect")
            _cancel_side_target(session, deps, row, "the side's position is gone")
            session.commit()
        return False
    size = min(units * anchor.ratio, held)  # units x the anchor's ratio, never past what's held
    result = orders.submit(
        deps.session_factory,
        deps.clients(),
        _stop_request(bot, run, replace(anchor, ratio=1), size, trigger),
        now=deps.now(),
        notify=deps.notify,
    )
    placed, waits = _placed(result), _held_for_freeze(result)
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        if not first and not waits:
            row.rearms += 1
        row.state = "working" if placed else ("pending" if waits else "failed")
        if placed:
            row.broker_order_id = result.broker_order_id
            row.contracts = units
        elif waits:
            row.broker_order_id = None  # the old order is gone: nothing of it is live
        _log(row, deps, event="placed" if first else "re-armed", size=size, result=result.message)
        session.commit()
    if not placed and not waits:
        _alert(
            deps,
            f"{bot.name}: stop not re-placed",
            f"The stop on {short.strip()} couldn't be re-placed ({result.message}). "
            "That side has no stop. Check your broker.",
            bot_id,
        )
    return placed


# A wing row that no longer holds its leg for anyone: sold, expired with the option, or
# cancelled before selling (the leg is the group's again).
_WING_DONE = ("filled", "expired", "cancelled")


def _watch_group(deps, exit_id: int) -> bool:
    """rearm for a group the app watches on its P&L (slice 13): watched again while
    this run still holds any of its legs, each in the group's direction (a sold leg held
    short, a bought one long), less what the leg-out handed to the group's wing rows and
    they haven't sold yet (theirs to sell, not this stop's). A wing row already sold
    ("filled") or ended with nothing to sell is left out: the positions already show it
    (review I-1: counted twice, a held unit would go unwatched). None held: nothing to watch,
    it expires with the group's target. Positions that can't be read keep it watched
    (its close sizes itself when it fires). Never sent, never a re-arm."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        legs = exit_legs(row)
        handed: dict[str, int] = {}
        for wing in session.scalars(
            select(BotExit).where(
                BotExit.run_id == row.run_id,
                BotExit.group_position == row.group_position,
                BotExit.kind == "wing",
                BotExit.state.not_in(_WING_DONE),
            )
        ):
            symbol = plain(_wing_leg(wing)[1].symbol)
            handed[symbol] = handed.get(symbol, 0) + wing.contracts
        run_id = row.run_id
    run, _ = _run_and_bot(deps, run_id)
    try:
        held = _positions(deps, run)
    except Exception:  # noqa: BLE001 -- a read error never stops the watch
        logger.exception("couldn't read positions to watch stop %s again", exit_id)
        return _watch_stop(deps, exit_id, True, None, again=True)
    own = {
        leg: held.get(plain(leg.symbol), 0) * (-1 if leg.side is Side.SELL else 1)
        - handed.get(plain(leg.symbol), 0)
        for leg in legs
    }
    if all(n <= 0 for n in own.values()):
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            row.state = "expired"
            _log(row, deps, event="none of the group's legs held; nothing to watch")
            _cancel_side_target(session, deps, row, "the group's position is gone")
            session.commit()
        return False
    units = max(-(-n // leg.ratio) for leg, n in own.items() if n > 0)  # a part unit counts
    return _watch_stop(deps, exit_id, True, units, again=True)


def _watch_stop(deps, exit_id: int, first: bool, held: int | None, again: bool = False) -> bool:
    """rearm for a watched run (bots slice 11): the stop is watched by the app again,
    sized to what's held; nothing is sent. Positions that can't be read keep it watched
    at its size (the close sizes itself to what's held when it fires): a read error
    never stops the watch. Counts as a re-arm unless `first`; `again` (a watched group)
    words it as watched again without counting. Returns True."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        if not first:
            row.rearms += 1
        row.state, row.broker_order_id = WATCHING, None
        if held is not None:
            row.contracts = min(held, row.contracts)
            event = "re-armed: watched by the app" if again or not first else "watched by the app"
        else:
            event = "couldn't read positions; still watched by the app at its size"
        _log(row, deps, event=event, size=row.contracts)
        session.commit()
    return True


def _expire_watched(deps, exit_id: int) -> None:
    """A watched stop whose option is past expiry: expired with the side's target,
    quietly (rearm's expiry evening)."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        if row.state != WATCHING:
            return
        row.state = "expired"
        _log(row, deps, event="the option expired; no longer watched")
        _cancel_side_target(session, deps, row, "the option expired")
        session.commit()


def group_units(session, run_row: BotRun, position: int) -> int:
    """A group's open units, by the app's own records: the run's contracts times the
    group's gcd, less the units closes you asked for closed of it ("reduced" rows log
    `closed`) (bots slice 6; manual_close's held accounting)."""
    _, gcd = exit_math.group_legs(run_row.legs, position)
    closed = sum(
        int(entry.get("closed", 0))
        for row in session.scalars(
            select(BotExit).where(
                BotExit.run_id == run_row.id,
                BotExit.group_position == position,
                BotExit.kind == "close",
                BotExit.state == "reduced",
            )
        )
        for entry in row.log
    )
    return max(run_row.contracts * gcd - closed, 0)


def exit_legs(row: BotExit) -> tuple[exit_math.GroupLeg, ...]:
    """The exit's legs per unit. Rows with no `legs` (test fixtures, rows a crash wrote
    mid-upgrade) fall back to the old short/long pair at ratio 1 (exit_math.row_legs)."""
    return exit_math.row_legs(row)


def group_columns(session, run_row: BotRun, position: int, rows) -> dict:
    """A new exit row's columns for a group (constraints): its legs per unit, and its
    anchor and kind as its stop or target recorded them (`rows`: the group's exit
    rows); a group with neither gets what place_protections would have made of it on this
    run's fill (exit_plan: a credit with one sold leg is on its short). The old columns hold
    the anchor or "", and the first other leg or ""."""
    own, _ = exit_math.group_legs(run_row.legs, position)
    model = next((r for r in rows if r.kind in ("stop", "target") and r.legs), None)
    if model is not None:
        anchor, app_watched = model.anchor_symbol, bool(model.watched)
        held = any(bool(r.app_held) for r in rows if r.kind == "stop")  # only a stop is held
    else:
        entry = run_row.fill_credit if run_row.fill_credit is not None else run_row.target_credit
        sole = exit_math.anchor_of(own) if exit_plan.credit_fill(entry) else None
        anchor, app_watched, held = (sole.symbol if sole else None), sole is None, False
    return dict(
        short_symbol=anchor or "",
        long_symbol=next((leg.symbol for leg in own if leg.symbol != anchor), ""),
        legs=[{"symbol": g.symbol, "side": g.side.value, "ratio": g.ratio} for g in own],
        anchor_symbol=anchor,
        watched=app_watched,
        app_held=held,
    )


def apply_time_action(deps, run_id: int, position: int, action, number: int, late: bool):
    """One time action on one side (time_actions.fire_time_actions and
    management.apply_override), under the lock: the stop and/or the target moved to
    the row's %. Returns (state, notes): "busy" (a close is running, or its exits are
    being placed: try again next pass), "closed", "done" (already applied), or the
    side's action row state, "filled" (everything moved), "failed" (anything not;
    never tried again) or "skipped" (due while you had the side's exits off: the action
    is spent, never applied later; bots slice 6), with what happened. A level it moves
    clears that level's per-trade change (its chip): the most recent change wins."""
    with EXITS_LOCK:
        with deps.session_factory() as session:
            run_row = session.get(BotRun, run_id)
            side = list(
                session.scalars(
                    select(BotExit).where(
                        BotExit.run_id == run_id, BotExit.group_position == position
                    )
                )
            )
            state = timing.side_state((r.kind, r.state) for r in side)
            if state != "open":
                return state, []
            if any(
                r.kind == "action" and r.log and r.log[0].get("sig") == action.sig for r in side
            ):
                return "done", []
            size = group_units(session, run_row, position)  # what's open, in units (5.7)
            cols = group_columns(session, run_row, position, side)
            g = _group_of(run_row, side, position, size, watched=cols["watched"])
            # A move onto something you switched off switches it back on (bots slice 18,
            # the author's ruling; Tracker2's management.write_time_action_override sets
            # stop_loss_off / profit_target_off False). The stop is checked against the
            # market first and stays off if it would fire at once (_revive_by_action).
            revive = {
                "stop": action.stop_pct is not None and stop_off(session, run_id, position),
                "target": action.target_pct is not None and target_off(session, run_id, position),
            }
            entry_credit = (
                run_row.fill_credit if run_row.fill_credit is not None else run_row.target_credit
            )
            stops_ = [r for r in side if r.kind == "stop" and r.state in STOP_LIVE]
            targets = [r for r in side if r.kind == "target" and r.state == "armed"]
            resting = [r for r in side if r.kind == "target" and r.state in ("working", "pending")]
            stop_id = max(r.id for r in stops_) if stops_ else None
            target_id = max(r.id for r in targets) if targets else None
        ask = pnl = None
        if action.stop_pct is not None and (stop_id is not None or revive["stop"]):
            # Nothing touched or recorded without a live price: tried again next pass.
            if g.anchor is None:  # a group the app watches: checked on its live P&L
                pnl = _live_pnl(deps, g)
                if pnl is None:
                    return "waiting", ["no fresh prices for the group's legs yet"]
            else:
                ask = _fresh_ask(deps, g.anchor.symbol)
                if ask is None:
                    return "waiting", ["no fresh quote for the short yet"]
        run, bot = _run_and_bot(deps, run_id)
        with deps.session_factory() as session:  # slice 21: in this trade's own stop style
            group = effective_group(session, run_id, bot, position)
        why = f"time action {number}"
        moves = []
        progress: dict = {}
        if action.stop_pct is not None and revive["stop"]:
            moves.append(("stop", lambda: _revive_by_action(
                deps, run_id, position, group, g, entry_credit, action.stop_pct, why, ask, pnl,
            )))  # fmt: skip
        elif action.stop_pct is not None:
            moves.append(("stop", lambda: _move_stop(
                deps, run, bot, group, stop_id, g, entry_credit, action, why, progress, ask,
                pnl=pnl,
            )))  # fmt: skip
        if action.target_pct is not None and revive["target"]:
            moves.append(("target", lambda: _target_back_on(
                deps, run_id, position, g, action, why
            )))  # fmt: skip
        elif action.target_pct is not None and resting:
            moves.append(("target", lambda: _move_resting_target(
                deps, run_id, position, g, action, why
            )))  # fmt: skip
        elif action.target_pct is not None:
            moves.append(("target", lambda: _move_target(
                deps, run_id, position, target_id, g, action, why
            )))  # fmt: skip
        if getattr(action, "close_pct", None):
            moves.append(("close", lambda: _time_close(deps, run_id, position, action, why)))
        notes, ok, moved_levels = [], True, {}
        for what, move in moves:
            try:
                moved, note = move()
            except Exception as e:  # noqa: BLE001 -- recorded and alerted once, never retried
                logger.exception("time action %s, run %s: moving the %s failed", number, run_id,
                                 what)  # fmt: skip
                moved, note = False, _broke_note(deps, stop_id, what, e, progress)
            ok, notes = ok and moved, [*notes, note]
            if moved and what in ("stop", "target"):  # a close (slice 7) clears no chip
                moved_levels[f"{what}_pct"] = None
                if what == "stop":  # slice 21: and this trade's own $ stop or closing order
                    moved_levels |= dict.fromkeys(STOP_STYLE)
        state = "filled" if ok else "failed"
        with deps.session_factory() as session:
            _record_action(deps, session, run_id, position, cols, size, action, number,
                           late, state, notes)  # fmt: skip
            override = session.scalar(
                select(BotTradeOverride).where(
                    BotTradeOverride.run_id == run_id, BotTradeOverride.group_position == position
                )
            )
            if override is not None and moved_levels:
                for name, value in moved_levels.items():
                    setattr(override, name, value)  # the time action's level is the latest
                session.commit()
        return state, notes


def _move_resting_target(deps, run_id: int, position: int, g: _Group, action, why):
    """(moved, note): a time action's target on a group whose target rests at TastyTrade
    (decision 0048): the new close-at as _move_target works it out, then
    change_resting_target (cancel, read back, place again). On a credit fill a target of
    100% or more is refused there (stops.target_limit)."""
    if g.entry is None:
        return False, "target not moved: the group has no fill prices"
    limit, problem = stops.target_limit(GroupDef(0, "", target_pct=action.target_pct), g.entry)
    if problem is not None:
        return False, f"target not moved: {problem}"
    moved, note = change_resting_target(deps, run_id, position, limit)
    return moved, (f"moved by {why}: {note}" if moved else f"target not moved: {note}")


def _revive_by_action(deps, run_id, position, group, g: _Group, entry_credit, pct, why,
                      ask, pnl) -> tuple[bool, str]:  # fmt: skip
    """A time action's stop move on a side whose stop you switched off (bots slice 18;
    Tracker2's write_time_action_override: stop_loss_off = False): the stop switched back
    on at the action's % (_moved_calc), checked first against the short's fresh ask (or a
    watched group's live P&L). Would fire at once, or can't be worked out: the stop stays
    off. Called with EXITS_LOCK held."""
    stop_id = revive_stop(deps, run_id, position, why)
    if stop_id is None:
        return False, "stop not moved: the stop is off and there's no stop row to switch back on"
    found, problem = _moved_calc(group, pct, g, entry_credit, why)
    if problem is None:
        calc, entry = found
        if g.anchor is None:
            problem = fires_at_once(calc.trigger, pnl)
        elif ask is None:
            problem = f"no fresh quote for the short to show a stop at {calc.trigger} won't fire"
        elif calc.trigger <= ask:
            problem = (
                f"a stop at {calc.trigger} is at or below the short's ask {ask}, so it would "
                "fire at once"
            )
    if problem is not None:
        unrevive_stop(deps, run_id, position, stop_id, problem)
        return False, f"the stop stays off: {problem}"
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        stop.contracts = min(stop.contracts, g.units) if g.units else stop.contracts
        _log(stop, deps, event=f"moved by {why}", **entry)
        session.commit()
    placed, note = place_revived(deps, stop_id, calc.trigger)
    return placed, f"{STOP_BACK_ON}: {note}"


def _target_back_on(deps, run_id, position, g: _Group, action, why) -> tuple[bool, str]:
    """A time action's target move on a side whose target you switched off: switched
    back on at the action's % (Tracker2: profit_target_off = False), armed on a new row
    (_move_target with none armed). Not moved: it stays off."""
    with deps.session_factory() as session:
        override = session.scalar(_override_of(run_id, position))
        override.target_off = False
        rests = target_rested(session, run_id, position)
        session.commit()
    if rests:  # decision 0048: placed again at TastyTrade, never armed
        moved, note = _resting_back_on(deps, run_id, position, g, action, why)
    else:
        moved, note = _move_target(deps, run_id, position, None, g, action, why)
    if not moved:
        with deps.session_factory() as session:
            session.scalar(_override_of(run_id, position)).target_off = True
            session.commit()
        return False, f"the target stays off: {note}"
    return True, f"the target is back on: {note}"


def _resting_back_on(deps, run_id: int, position: int, g: _Group, action, why):
    """(placed, note): a resting target you switched off, placed again at TastyTrade at the
    time action's level: the level written on its cancelled row, then place_target_again."""
    if g.entry is None:
        return False, "target not moved: the group has no fill prices"
    limit, problem = stops.target_limit(GroupDef(0, "", target_pct=action.target_pct), g.entry)
    if problem is not None:
        return False, f"target not moved: {problem}"
    with deps.session_factory() as session:
        latest = max(session.scalars(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.group_position == position,
            BotExit.kind == "target")), key=lambda r: r.id)  # fmt: skip
        if latest.state != "cancelled":
            return False, "target not moved: there's no switched-off target to put back"
        old, latest.limit = latest.limit, limit
        _log(latest, deps, event=f"moved by {why}", target=f"{action.target_pct:f}",
             unit="pct", credit=f"{g.entry:f}", close_at=f"{limit:f}", was=f"{old}")  # fmt: skip
        session.commit()
    return place_target_again(deps, run_id, position, g.units)


def _record_action(deps, session, run_id, position, cols: dict, size, action, number, late,
                   state: str, notes: list) -> None:  # fmt: skip
    """A time action's row on one group (its columns: group_columns; `size` in units):
    what it did, known by what it says (sig)."""
    now = deps.now()
    session.add(BotExit(
        run_id=run_id, group_position=position, kind="action", state=state,
        **cols, contracts=size, rearms=0,
        log=[{"at": now.isoformat(), "sig": action.sig, "rule": action.label,
              "number": number, "late": late, "notes": notes}],
        created_at=now, updated_at=now,
    ))  # fmt: skip
    session.commit()


def _time_close(deps, run_id: int, position: int, action, why: str) -> tuple[bool, str]:
    """(started, note): a time action's close % of the group (bots slice 7;
    time_actions.close_contracts and execute_time_action), after its moves: the % of
    the group's original units, rounded down, never leaving fewer than 1 open (0
    closes nothing, and isn't a failure). Any group: it counts units, never one leg's
    contracts. It runs as slice 6's part close, so what's left keeps the stop and
    target just moved. Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        run_row = session.get(BotRun, run_id)
        _, gcd = exit_math.group_legs(run_row.legs, position)
        original = run_row.contracts * gcd
        held, live = group_units(session, run_row, position), run_row.live
    n = watch.close_contracts(action.close_pct, original, held)
    if n <= 0:
        return True, f"nothing closed: at least 1 of the {held} stays open"
    if not live:
        return False, "close not sent: the trade ran in preview"
    if not _session_open(deps) or _frozen(deps):
        return False, "close not sent: the market isn't in its regular session, or Freeze is on"
    if fire_close(deps, run_id, position, n, held, by=why) is None:
        return False, "close not started: the side is closing or closed"
    return True, f"closing {n} of {held}"


QUOTE_WAITS = 3  # fresh-quote reads for a stop move's live check, 1 s apart


def _fresh_ask(deps, symbol: str) -> Decimal | None:
    """The short's ask from a fresh quote, waiting briefly for one (the stream may
    only just have been subscribed). None if none comes."""
    for attempt in range(QUOTE_WAITS):
        quote = _quote(deps, symbol)  # subscribes it too
        if quote is not None and quote.ask is not None and quote.ask > 0:
            return quote.ask
        if attempt < QUOTE_WAITS - 1:
            deps.sleep(1)
    return None


def stream_healthy(deps, source: str, underlying: str, now: datetime) -> bool:
    """watch_loop._stream_live: the stream the quote came through is up and the
    underlying is updating through it (a fresh quote from that source), so a quiet
    option's quote is a current one (review M-4; slice 10's stream_ok). The watched stops
    and the watch rules (#202) both read it."""
    status = deps.hub.status() if hasattr(deps.hub, "status") else {}
    if status.get(source) != "streaming":
        return False
    if hasattr(deps.hub, "get"):
        spot = deps.hub.get(source, underlying)
    else:
        spot = deps.hub.latest(underlying)
        spot = spot if spot is not None and spot.source == source else None
    at = spot and (spot.last_at or spot.at)
    return at is not None and timedelta(0) <= now - at <= exit_math.STALE_AFTER


def quotes_live(deps, quotes, underlying: str, now: datetime) -> bool:
    """Every quote there and every stream they came through live (stream_healthy, each
    source read once): a group's quiet quotes are its current ones (#202)."""
    quotes = list(quotes)
    if not quotes or any(q is None for q in quotes):
        return False
    live: dict[str, bool] = {}
    for quote in quotes:
        if quote.source not in live:
            live[quote.source] = stream_healthy(deps, quote.source, underlying, now)
    return all(live.values())


def _age(stamp, now: datetime) -> str:
    return f"{(now - stamp).total_seconds():.0f}s" if stamp is not None else "never"


def stale_detail(deps, symbols, underlying: str, now: datetime) -> str:
    """For the WARNING logged with a pause (Tracker2's _log_stale_detail, #203): each leg's
    quote and Greeks age and source, and each stream's state and whether it's live
    (stream_healthy on `underlying`), so a log tells a quiet option from a dead feed.
    Never raises."""
    try:
        legs = []
        reader = getattr(deps.hub, "delta", None)
        for symbol in symbols:
            quote = deps.hub.latest(symbol)
            delta = reader(symbol) if reader is not None else None
            legs.append(
                f"{symbol} quote={_age(quote.at if quote else None, now)} "
                f"greeks={_age(delta[1] if delta else None, now)} "
                f"src={quote.source if quote else '?'}"
            )
        status = deps.hub.status() if hasattr(deps.hub, "status") else {}
        streams = ", ".join(
            f"{name}={state} "
            f"({'live' if stream_healthy(deps, name, underlying, now) else 'not live'})"
            for name, state in sorted(status.items())
        )
        return f"legs: {', '.join(legs) or 'none'} | streams: {streams or 'none'}"
    except Exception:  # noqa: BLE001 -- diagnostics never break the pass
        logger.exception("couldn't describe the stale quotes")
        return "legs: unknown"


def _broke_note(deps, stop_id, what: str, e: Exception, progress: dict) -> str:
    """What a move that broke part-way left behind, for the alert, by the stop row's
    actual state."""
    name = e.__class__.__name__
    if what == "stop" and progress.get("cancelled"):
        try:
            with deps.session_factory() as session:
                row = session.get(BotExit, stop_id)
                state = row.state if row is not None else None
        except Exception:  # noqa: BLE001 -- can't tell: say so
            state = None
        if state == "pending":
            return (
                f"the old stop was cancelled and no new one was placed ({name}): that side "
                "has no stop until the app's next check places it. Check your broker"
            )
        if state == "working":
            return f"the stop was moved, then something went wrong ({name}). Check your broker"
        return (
            f"the old stop was cancelled and no new one was placed ({name}): that side has "
            "no stop. Check your broker"
        )
    if what == "stop" and progress.get("cancel_sent"):
        return (
            f"the old stop's cancel was sent and then something went wrong ({name}): that "
            "side may have no stop. Check your broker"
        )
    return (
        f"the {what} wasn't moved: something went wrong part-way ({name}); check the "
        f"side's {what} at your broker"
    )


@dataclass(frozen=True)
class _Group:
    """A group, for moving its stop (slice 13): its legs per unit, its anchor (the leg a
    resting stop is on; None for a group the app watches on its P&L), its open units,
    the run's fills (symbol -> fill dict), its entry per unit (+ a credit; None while a
    leg has no fill price) and when the run filled (a live price must be newer)."""

    legs: tuple[exit_math.GroupLeg, ...]
    anchor: exit_math.GroupLeg | None
    units: int
    fills: dict
    entry: Decimal | None
    filled_at: datetime | None = None


def _group_of(run_row: BotRun, rows, position: int, units: int,
              watched: bool = False) -> _Group:  # fmt: skip
    """The group as its stop recorded it (its legs, anchor and kind), else as its
    target did, else from the run's legs (`watched`: group_columns' kind for it)."""
    stop = max((r for r in rows if r.kind == "stop"), key=lambda r: r.id, default=None)
    model = stop or next((r for r in rows if r.kind == "target"), None)
    if model is not None:
        legs = exit_legs(model)
        anchor = None if model.watched else _anchor_leg(model)
    else:
        legs, _ = exit_math.group_legs(run_row.legs, position)
        anchor = None if watched else exit_math.anchor_of(legs)
    fills = {f["symbol"]: f for f in run_row.filled_legs or ()}
    return _Group(tuple(legs), anchor, units, fills, group_entry_of(run_row, legs),
                  run_row.filled_at)  # fmt: skip


_LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def _live_pnl(deps, g: _Group) -> Decimal | None:
    """The group's P&L per unit now, at its legs' mids, by the watched stop's own rules
    (watched_stops.evaluate_group: every leg fresh and newer than the fill, a bought leg
    at 0/0 counts 0), waiting briefly for prices as _fresh_ask does. None without an
    entry or a usable fresh price on every leg: there's no telling."""
    if g.entry is None:
        return None
    for attempt in range(QUOTE_WAITS):
        quotes = [(leg, _quote(deps, leg.symbol)) for leg in g.legs]  # subscribes them too
        if all(quote is not None for _, quote in quotes):
            reading = watched_stops.evaluate_group(
                "", quotes, g.entry, Decimal(0), deps.now(), g.filled_at or _LONG_AGO
            )
            if reading.pnl is not None:
                return reading.pnl
        if attempt < QUOTE_WAITS - 1:
            deps.sleep(1)
    return None


def fires_at_once(level: Decimal, pnl: Decimal | None) -> str | None:
    """Why a watched level can't be set now: with no live P&L there's no telling, and a
    level at or above it is already reached (management._group_stop_live_check, on
    the group's P&L)."""
    if pnl is None:
        return (
            f"no fresh prices for the group's legs to show a stop at P&L "
            f"{watched_stops.signed(level)} wouldn't fire at once"
        )
    if level >= pnl:
        return (
            f"a stop at P&L {watched_stops.signed(level)} is at or above the group's P&L "
            f"{watched_stops.signed(pnl)} now, so it would fire at once"
        )
    return None


def _moved_calc(group, pct, g: _Group, entry_credit, why: str, usd: Decimal | None = None):
    """((calc, its log entry), None), or (None, why it can't be used). A resting stop:
    at `pct` in the group's own % style (a $ or closing-order group uses % above the
    anchor's fill), priced per contract of its anchor's ratio, without the
    closing-order overlay: the most recent change wins. A group the app watches: its
    P&L level at `pct` of the entry lost (stops.watched_level). Bots slice 21 (Tracker2's
    per-trade stop_loss_unit "usd"): `usd` instead of a %, the loss per contract: the
    anchor's fill plus the dollars (stops.usd_trigger), or a watched level at -$."""
    if g.anchor is None:
        if g.entry is None:
            return None, "the group has no fill prices"
        calc = stops.action_move(group, pct, g.entry, entry_credit, why, usd=usd)
    else:
        short_fill = _fill_of(g.fills, g.anchor.symbol)
        if short_fill is None:
            return None, "the short has no fill price"
        calc = stops.action_move(group, pct, g.entry, entry_credit, why, anchor=g.anchor.symbol,
                                 anchor_fill=short_fill, ratio=g.anchor.ratio, usd=usd)  # fmt: skip
    if calc.problem is not None:
        return None, calc.problem
    entry = calc.as_log()
    entry["stop_calc"]["moved_by"] = why
    return (calc, entry), None


def _move_stop(deps, run: _Run, bot, group, stop_id, g: _Group, entry_credit, action, why,
               progress: dict | None = None, ask: Decimal | None = None,
               marker: str = MOVING_STOP, found=None, pnl: Decimal | None = None):  # fmt: skip
    """(moved, note). The new trigger in the group's own % style (a $ or closing-order
    group uses % above the anchor's fill), without the closing-order overlay: the most
    recent change wins.

    Decided before anything is cancelled (management._group_stop_live_check): a
    trigger at or below the short's live ask would fire at once, and without a fresh
    quote there's no telling, so either is refused with the old stop still working.
    Then the stop is marked (MOVING_STOP) before its cancel is sent, so a crash
    leaves a row recovery puts back; once the cancel is confirmed it waits as
    "pending" at the new trigger and is placed (rearm(first=True): sized to what's
    held, not a re-arm). A placement that's refused leaves it "pending" for the
    monitor's next check, with an alert. If the old one filled, can't be confirmed
    cancelled or its cancel wasn't sent, nothing moves and the old stop stands.
    `progress` says how far it got, for the caller's alert if it breaks part-way;
    `marker` is the event recovery knows the mid-move stop by (MOVING). `found`: the
    new (calc, log entry) already worked out (a profit action's, slice 7); otherwise
    it's the action's % in the group's own style (_moved_calc).

    A group the app watches on its P&L (slice 13; `g` with no anchor) has
    nothing at the broker: its new level is written and nothing is cancelled or sent.
    It's checked against `pnl`, the group's live P&L (_live_pnl): a level at or above
    it would fire at once, and without it there's no telling."""
    progress = {} if progress is None else progress
    if stop_id is None:
        return False, "stop not moved: the side has no working stop"
    if found is None:
        found, problem = _moved_calc(group, action.stop_pct, g, entry_credit, why)
        if problem is not None:
            return False, f"stop not moved: {problem}"
    calc, entry = found
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        state, order_id, old = stop.state, stop.broker_order_id, stop.trigger
    if calc.trigger == old:
        return True, f"stop already at {old}"
    if g.anchor is None:
        return _move_level(deps, stop_id, calc, entry, old, why, pnl)
    if ask is None:  # the caller waited for a fresh one (_fresh_ask)
        return False, (
            f"stop not moved: no fresh quote for the short to show a stop at {calc.trigger} "
            "wouldn't fire at once; the old stop still stands"
        )
    if calc.trigger <= ask:
        return False, (
            f"stop not moved: a stop at {calc.trigger} is at or below the short's ask {ask}, "
            "so it would fire at once; the old stop still stands"
        )
    if state in ("pending", WATCHING):  # nothing at the broker: the new trigger is all
        with deps.session_factory() as session:
            stop = session.get(BotExit, stop_id)
            stop.trigger = calc.trigger
            _log(stop, deps, event=f"moved by {why}", **entry)
            session.commit()
        if state == WATCHING:  # watched by the app at its new level from the next pass
            return True, f"stop {old} → {calc.trigger} ({WATCHED})"
        # Moved, but not protecting yet: said so, which makes it a safety alert.
        return True, f"stop {old} → {calc.trigger}, {NOT_AT_BROKER}"
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        stop.state = "cancelled"  # before the cancel: recovery puts it back after a crash
        _log(stop, deps, event=marker, to=f"{calc.trigger:f}")
        session.commit()

    def stands(note: str) -> tuple[bool, str]:
        with deps.session_factory() as session:
            stop = session.get(BotExit, stop_id)
            stop.state = "working"  # the old order: the monitor reads it
            _log(stop, deps, event=f"not moved: {note}")
            session.commit()
        return False, f"stop not moved: {note}"

    cancelled = orders.cancel(
        deps.session_factory, deps.clients(), run.broker, run.account, order_id, "bot", bot.id,
        now=deps.now(), notify=deps.notify,
    )  # fmt: skip
    if cancelled.action == "refuse":
        return stands(f"its cancel wasn't sent ({cancelled.message}); the old stop still stands")
    progress["cancel_sent"] = True
    try:
        found = read_after_cancel(deps, lambda: _broker(deps, run).get_order(run.account, order_id))
    except Exception:  # noqa: BLE001 -- can't tell: the monitor re-reads it
        logger.exception("couldn't read stop %s back after its cancel", stop_id)
        return stands("the old stop couldn't be read back; the monitor re-reads it")
    if found.status is OrderStatus.FILLED:
        return stands("it filled first")
    if found.may_be_working:
        return stands("the old stop's cancel wasn't confirmed; it still stands")
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        stop.trigger, stop.state, stop.broker_order_id = calc.trigger, "pending", None
        _log(stop, deps, event=f"moved by {why}", **entry)
        session.commit()
    progress["cancelled"] = True  # from here a breakage leaves it "pending" for the monitor
    return _place_pending(deps, stop_id, old, calc.trigger)


def _move_level(deps, stop_id, calc, entry, old, why, pnl) -> tuple[bool, str]:
    """A watched group's stop moved: its new P&L level written, nothing cancelled or
    sent (the watch reads it from the next pass), unless it would fire at once."""
    problem = fires_at_once(calc.trigger, pnl)
    if problem is not None:
        return False, f"stop not moved: {problem}; the old stop still stands"
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        stop.trigger = calc.trigger
        _log(stop, deps, event=f"moved by {why}", **entry)
        session.commit()
    return True, f"stop {old} → {calc.trigger} ({WATCHED})"


def place_stop_at(deps, stop_id, group, g: _Group, entry_credit, pct, ask: Decimal | None, why: str,
                  pnl: Decimal | None = None, found=None):  # fmt: skip
    """(placed, note): a side whose stop isn't working or pending (it failed, or was
    cancelled and not put back) gets a new one at `pct`, checked against the short's
    fresh ask first (bots slice 6: your change, or turning exits back on); for a group
    the app watches, against its live P&L (`pnl`). It's written "pending" at the new
    trigger, then placed as a moved stop is (a watched group is watched again, nothing
    sent). `found`: the new (calc, log entry) already worked out (bots slice 21: a $ stop
    or a closing order). Called with EXITS_LOCK held."""
    if found is None:
        found, problem = _moved_calc(group, pct, g, entry_credit, why)
        if problem is not None:
            return False, f"stop not placed: {problem}"
    calc, entry = found
    if g.anchor is None:
        problem = fires_at_once(calc.trigger, pnl)
        if problem is not None:
            return False, f"stop not placed: {problem}"
    elif ask is None:
        return False, f"stop not placed: no fresh quote for the short to check {calc.trigger}"
    elif calc.trigger <= ask:
        return False, (
            f"stop not placed: a stop at {calc.trigger} is at or below the short's ask {ask}, "
            "so it would fire at once"
        )
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        old = stop.trigger
        stop.trigger, stop.state, stop.broker_order_id = calc.trigger, "pending", None
        _log(stop, deps, event=f"moved by {why}", **entry)
        session.commit()
    return _place_pending(deps, stop_id, old, calc.trigger)


def _place_pending(deps, stop_id, old, trigger) -> tuple[bool, str]:
    """Place a stop just written "pending" at its new trigger (rearm(first=True): sized
    to what's held, not a re-arm). Refused: it stays "pending" for the monitor's next
    check (once more), and the note says the side has no stop until then."""
    if rearm(deps, stop_id, first=True):
        return True, f"stop {old} → {trigger}"
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        after = stop.state
        if after == "failed":  # refused: the monitor's next check places it (first=True)
            stop.state, stop.broker_order_id = "pending", None
            _log(stop, deps, event="the new stop was refused; tried again at the next check")
            session.commit()
    if after == "expired":
        return True, "stop not needed: the side's position is gone"
    if after == "pending" and _frozen(deps):
        return True, f"stop {old} → {trigger}, placed when Freeze is off"
    return False, (
        f"the old stop was cancelled and the new one at {trigger} wasn't placed: that "
        "side has no stop until the app's next check places it (within 30 seconds)"
    )


_HELD_SAID: set[tuple[int, int]] = set()  # (run, side) whose hold has been alerted, once
SETTLED = "settled: the close is over at the broker"  # lifts held_back for its close


def settle_held(deps) -> None:
    """Each monitor pass (bots slice 6): a side held back because an earlier close may
    still be live (it ended unclear, or recovery found its order live) is released
    once the broker shows that close over (settle_side). Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        candidates = {
            (r.run_id, r.group_position)
            for r in session.scalars(
                select(BotExit).where(
                    (BotExit.kind == "stop") & (BotExit.state == "cancelled")
                    | (BotExit.kind.in_(CLOSES) & (BotExit.state == "failed"))
                )
            )
        }
    for run_id, position in sorted(candidates):
        try:
            settle_side(deps, run_id, position)
        except Exception:  # noqa: BLE001 -- one side never stops the others
            logger.exception("couldn't settle held-back run %s side %s", run_id, position)


def settle_side(deps, run_id: int, position: int, asked: bool = False) -> tuple[bool, str]:
    """(released, what happened) for one held-back side. Conservative: it never sends
    an order, never reads positions, never marks the side closed and never touches a
    stop, a target or a wing. Skipped while a close job on the side is still finishing
    (FIRING). It keeps holding while any working order at the broker touches the
    side's legs, or any recorded close order may be working or can't be read (alerted
    once a side; said to you when `asked`: the run page's re-check). Otherwise the
    hold is lifted: the close row gets SETTLED, and it alerts once that the side is
    released with no stop put back, for you to set one (or close). Called with
    EXITS_LOCK held."""
    with deps.session_factory() as session:
        rows = list(
            session.scalars(
                select(BotExit).where(BotExit.run_id == run_id, BotExit.group_position == position)
            )
        )
        if held_back(rows) != "close" or timing.side_state((r.kind, r.state) for r in rows) != (
            "open"
        ):
            return False, "Nothing on this side is held back."
        if any(r.id in FIRING for r in rows):
            return False, "A close on this side is still finishing; try again in a moment."
        close = _latest(rows, *CLOSES)
        unclear = _unclear_target(rows)
        unclear_id = unclear.id if unclear is not None else None
        closes = [r for r in rows if r.kind in CLOSES and r.state == "failed"]
        order_ids = [r.broker_order_id for r in closes if r.broker_order_id]
        order_ids += [e["market_order"] for r in closes for e in r.log if e.get("market_order")]
        # Every leg of the group, from a row that records the whole group (not a wing).
        legs = {plain(leg.symbol) for leg in exit_legs(_group_row(rows))}
    run, bot = _run_and_bot(deps, run_id)
    side_name = next((g.name for g in bot.groups if g.position == position), None) or (
        f"Group {position + 1}"
    )
    waiting = None
    try:
        # An order the app never recorded (a market buy-back it couldn't write down)
        # still shows here: any working order on any of the group's legs keeps it held.
        for found in _broker(deps, run).working_orders(run.account):
            if any(plain(leg.symbol) in legs for leg in found.legs):
                waiting = f"order {found.broker_order_id} on its legs is still working"
                break
    except Exception:  # noqa: BLE001 -- can't tell: keep holding
        logger.exception("couldn't read working orders to settle a held-back side")
        waiting = "your working orders couldn't be read"
    for order_id in order_ids if waiting is None else ():
        try:
            found = _broker(deps, run).get_order(run.account, order_id)
        except Exception:  # noqa: BLE001 -- can't tell: keep holding
            logger.exception("couldn't read close order %s of a held-back side", order_id)
            waiting = f"its order {order_id} couldn't be read"
            break
        if found.may_be_working:
            waiting = f"its order {order_id} may still be working"
            break
    key = (run_id, position)
    if waiting is not None:
        what = "A profit target" if unclear_id is not None else "An earlier close"
        said = (
            f"{what} on {side_name} may still be working at your broker "
            f"({waiting}), so the app isn't sending anything for it. It checks again "
            "every 30 seconds. Check your broker."
        )
        if not asked and key not in _HELD_SAID:
            _HELD_SAID.add(key)
            _alert(deps, f"{bot.name}: a side is held back", said, bot.id, run_id=run_id)
        return False, said
    for settled in {r for r in (close.id if close else None, unclear_id) if r is not None}:
        with deps.session_factory() as session:
            _log(session.get(BotExit, settled), deps, event=SETTLED)
            session.commit()
    _HELD_SAID.discard(key)
    said = (
        f"{side_name} is no longer held back: the earlier close on it is over at "
        "your broker. The app didn't put a stop back — check your broker, then set a stop "
        "(or close) from the run page."
    )
    _alert(deps, f"{bot.name}: {side_name} is no longer held back", said, bot.id, run_id=run_id)
    return True, said


def _group_row(rows: list[BotExit]) -> BotExit:
    """A row that records the whole group's legs: its stop, target or a close, never a
    wing (which records only its own leg)."""
    return next((r for r in rows if r.kind != "wing"), rows[0])


def _recover_moving(deps) -> None:
    """A stop left "cancelled" by a time action that never finished (the app died
    between its cancel and the new one; MOVING_STOP is its last event). Read the old
    order: still working, filled, or unreadable: back to "working" for the monitor;
    gone: placed again at its old trigger (the time action, never recorded, runs
    again). Called with EXITS_LOCK held, so a move in progress is never seen here."""
    with deps.session_factory() as session:
        stuck = [
            (r.id, r.run_id, r.broker_order_id)
            for r in session.scalars(
                select(BotExit).where(BotExit.kind == "stop", BotExit.state == "cancelled")
            )
            if r.log and r.log[-1].get("event") in MOVING
        ]
    for stop_id, run_id, order_id in stuck:
        try:
            run, bot = _run_and_bot(deps, run_id)
            try:
                found = _broker(deps, run).get_order(run.account, order_id) if order_id else None
            except Exception:  # noqa: BLE001 -- can't tell: the monitor re-reads it
                logger.exception("couldn't read stop %s after a cut-off move", stop_id)
                found = "unread"
            with deps.session_factory() as session:
                stop = session.get(BotExit, stop_id)
                if found == "unread":
                    stop.state = "working"  # the monitor re-reads it
                    _log(stop, deps, event="a move was cut off; the old stop couldn't be read")
                    session.commit()
                    tail = "The old stop couldn't be confirmed; the app keeps checking it."
                elif found is not None and (
                    found.status is OrderStatus.FILLED or found.may_be_working
                ):
                    stop.state = "working"
                    _log(stop, deps, event="a move was cut off; the old stop is still there")
                    session.commit()
                    tail = "The old stop is still in place."
                else:
                    stop.state, stop.broker_order_id = "pending", None
                    _log(stop, deps, event="a move was cut off after its cancel; placing it again")
                    session.commit()
                    placed = rearm(deps, stop_id, first=True)
                    tail = (
                        "Its stop was placed again." if placed
                        else "Its stop couldn't be placed again: that side may have no stop."
                    )  # fmt: skip
            _alert(
                deps,
                f"{bot.name}: a stop move was cut off",
                f"Moving the stop on a side was cut off part-way. {tail} Check your broker.",
                bot.id,
            )
        except Exception:  # noqa: BLE001 -- one stop never stops the others
            logger.exception("couldn't recover stop %s after a cut-off move", stop_id)


def _move_target(deps, run_id: int, position: int, target_id, g: _Group, action, why):
    """(moved, note): the group's armed target closes at the new % of its entry
    (stops.target_limit, as place_protections prices it: a credit kept, or a debit's
    profit); a group with no armed target gets one."""
    credit = g.entry
    if credit is None:
        credit = exit_math.group_entry(
            g.legs, {leg.symbol: _fill_of(g.fills, leg.symbol) for leg in g.legs
                     if _fill_of(g.fills, leg.symbol) is not None},
        )  # fmt: skip
    if credit is None:
        return False, "target not moved: the group has no fill prices"
    limit, problem = stops.target_limit(GroupDef(0, "", target_pct=action.target_pct), credit)
    if problem is not None:
        return False, f"target not moved: {problem}"
    now = deps.now()
    entry = {"event": f"moved by {why}", "target": f"{action.target_pct:f}", "unit": "pct",
             "credit": f"{credit:f}", "close_at": f"{limit:f}"}  # fmt: skip
    with deps.session_factory() as session:
        if target_id is not None:
            target = session.get(BotExit, target_id)
            old = target.limit
            target.limit = limit
            _breaches.pop(target_id, None)
            _log(target, deps, **entry)
            session.commit()
            return True, f"target {old} → {limit}"
        anchor = g.anchor.symbol if g.anchor else None
        session.add(BotExit(
            run_id=run_id, group_position=position, kind="target", state="armed",
            short_symbol=anchor or "",
            long_symbol=next((leg.symbol for leg in g.legs if leg.symbol != anchor), ""),
            legs=[{"symbol": leg.symbol, "side": leg.side.value, "ratio": leg.ratio}
                  for leg in g.legs],
            anchor_symbol=anchor, watched=g.anchor is None, contracts=g.units,
            limit=limit, rearms=0, log=[{"at": now.isoformat(), **entry}],
            created_at=now, updated_at=now,
        ))  # fmt: skip
        session.commit()
    return True, f"target set at {limit}"


def watch_pass(deps) -> None:
    """Every 2 s in the session: fire armed targets on two fresh passes at or past
    their limit, read on the group's natural value over every leg (watch_loop's
    armed-exit pass; spec correction 1). A quiet quote counts while its stream is live
    (#202: `quiet_ok`, as the watch rules read it). A target without fresh prices for a
    minute is logged once, never alerted (_track_target_stale, #203)."""
    now = deps.now()
    if _frozen(deps) or session_now("SPY", now.astimezone(MARKET_TZ)) != "regular":
        with _target_lock:
            _target_stale_since.clear()
            _target_stale_logged.clear()
        return
    with deps.session_factory() as session:
        armed = []
        for r in session.scalars(
            select(BotExit).where(BotExit.kind == "target", BotExit.state == "armed")
        ):
            run = session.get(BotRun, r.run_id)
            bot = session.get(Bot, run.bot_id)
            underlying = underlyings.watch_symbol(bot.underlying if bot else None) or "SPX"
            armed.append((r.id, exit_legs(r), r.limit, run.filled_at, underlying))
    for exit_id, legs, limit, filled_at, underlying in armed:
        try:
            symbols = {leg.symbol for leg in legs}
            deps.hub.subscribe(symbols | {underlying})
            quotes = {symbol: deps.hub.latest(symbol) for symbol in symbols}
            quiet_ok = quotes_live(deps, quotes.values(), underlying, now)
            ok = filled_at is not None and exit_math.natural_fresh(
                legs, quotes, now, filled_at, quiet_ok
            )
            _track_target_stale(deps, exit_id, ok, sorted(symbols), underlying, now)
            value = exit_math.group_value(legs, quotes, natural=True) if ok else None
            if value is not None and value <= limit:
                _breaches[exit_id] = _breaches.get(exit_id, 0) + 1
            else:
                _breaches.pop(exit_id, None)
            if _breaches.get(exit_id, 0) >= exit_math.CONFIRM_PASSES:
                _breaches.pop(exit_id, None)
                fire_target(deps, exit_id)
        except Exception:  # noqa: BLE001 -- one target never stops the others
            logger.exception("couldn't watch bot target %s", exit_id)
    with _target_lock:  # a target no longer armed starts afresh
        ids = {a[0] for a in armed}
        for exit_id in [i for i in _target_stale_since if i not in ids]:
            del _target_stale_since[exit_id]
        _target_stale_logged.intersection_update(ids)


# Tracker2's QUIET_STALE_KINDS (b57aab7c): an app-watched profit target with no fresh
# price only delays a take-profit, so its pause is logged, not pushed, and its recovery
# is silent. The stops and watch rules alert on their own.
_target_stale_since: dict[int, datetime] = {}  # target id -> first pass without prices
_target_stale_logged: set[int] = set()
_target_lock = threading.Lock()


def _track_target_stale(deps, exit_id: int, ok: bool, symbols, underlying: str, now) -> None:
    with _target_lock:
        if ok:
            _target_stale_since.pop(exit_id, None)
            _target_stale_logged.discard(exit_id)
            return
        since = _target_stale_since.setdefault(exit_id, now)
        if now - since < watch.STALE_ALERT_AFTER or exit_id in _target_stale_logged:
            return
        _target_stale_logged.add(exit_id)
    logger.warning(
        "bot target %s: profit target paused, no fresh quote for %ss (not alerted) | %s",
        exit_id, int((now - since).total_seconds()), stale_detail(deps, symbols, underlying, now),
    )  # fmt: skip


def fire_target(deps, exit_id: int) -> None:
    """Once per side: claim the side under the lock (target "firing", its stop
    marked cancelled), then close it on its own thread (close_jobs.begin_close,
    enqueue): cancel the stop and confirm, close the side and chase it; if it
    doesn't fill, put the stop back or, if the bot says so, go to the natural
    price and leg out."""
    with EXITS_LOCK:
        claim = _claim_target(deps, exit_id)
        if claim is None:
            return
        FIRING.add(exit_id)
    run_id, bot_id, stop_id = claim
    try:
        deps.background(_target_job, deps, exit_id, run_id, bot_id, stop_id)
    except Exception:  # noqa: BLE001 -- no thread: put the stop back and say so
        logger.exception("couldn't start the close for target %s", exit_id)
        try:
            with EXITS_LOCK:
                _recover(deps, exit_id, bot_id, stop_id, False, "couldn't start the close")
        finally:
            FIRING.discard(exit_id)


def fire_timed(deps, run_id: int, position: int, note: str) -> int | None:
    """The timed close of one side (timed_close.fire_timed_closes, close_jobs.begin_close):
    claimed under the lock exactly as a target close is, so a side never has two
    closing orders, then closed on its own thread by the same job. Returns the timed
    close's row id, or None if the side can't be claimed (closed, or busy)."""
    with EXITS_LOCK:
        claim = _claim_side(deps, run_id, position, "timed", f"timed close: {note}")
        if claim is None:
            return None
        FIRING.add(claim[0])
    return _start_claimed(deps, run_id, claim)


def fire_close(deps, run_id: int, position: int, quantity: int, open_size: int,
               by: str | None = None, then_stop: dict | None = None,
               detail: str | None = None, order_type: str | None = None,
               wait: bool = False, floor: Decimal | None = None) -> int | None:  # fmt: skip
    """A close of `quantity` contracts of one side that you asked for (bots slice 6;
    manual_close.begin_manual_close): claimed under the lock exactly as a timed close
    (its target and stop pulled, one closing order per side), then closed on its own
    thread by the same job, sized to what's held and priced from the mid. What's left
    open is protected again for exactly that (_close_filled). `open_size`: the side's
    open size by the app's records. Returns the row's id, or None if the side can't
    be claimed.

    Slice 7: `by` names the rule that started it ("S/L ratio exit", "profit action 1",
    "time action 2"; None: you); `then_stop` ({"pct" or "usd", "by"}) is a profit action's stop
    move, applied to what's left once the close fills (_then_stop). Slice 11: `detail`,
    the reading that started it (a watched stop's), logged with it.

    Bots slice 18: `order_type` "market" (manual_close.MARKET) sends a one-leg group's
    close at market once its stop's cancel is confirmed (_market_close); `wait` runs the
    close on this thread and returns once it has ended (Close entire trade's job closes
    one group after another)."""
    note = f"a close of {quantity} for the {by}" if by else f"a close of {quantity} you asked for"
    extra = {"by": by} if by else {}
    if then_stop:
        extra["then_stop"] = then_stop
    if detail:
        extra["reading"] = detail
    if order_type == MARKET:
        extra["order_type"] = MARKET
    if floor is not None:  # bots slice 21: a paired close's "sold at a limit" price
        extra["floor"] = f"{floor:f}"
    with EXITS_LOCK:
        claim = _claim_side(
            deps, run_id, position, "close", note, contracts=quantity, open=open_size, **extra,
        )  # fmt: skip
        if claim is None:
            return None
        FIRING.add(claim[0])
    if wait:
        exit_id, bot_id, stop_id = claim
        _target_job(deps, exit_id, run_id, bot_id, stop_id)
        return exit_id
    return _start_claimed(deps, run_id, claim)


MARKET = "market"  # manual_close.MARKET


def _start_claimed(deps, run_id: int, claim) -> int:
    """Run a claimed close's job in the background; with no thread, put the stop and
    target back and say so."""
    exit_id, bot_id, stop_id = claim
    try:
        deps.background(_target_job, deps, exit_id, run_id, bot_id, stop_id)
    except Exception:  # noqa: BLE001 -- no thread: put the stop and target back and say so
        logger.exception("couldn't start the close %s", exit_id)
        try:
            with EXITS_LOCK:
                _recover(deps, exit_id, bot_id, stop_id, False, "couldn't start the close")
        finally:
            FIRING.discard(exit_id)
    return exit_id


def _claim_side(deps, run_id: int, position: int, kind: str, note: str,
                contracts: int | None = None, **logged):  # fmt: skip
    """(row id, bot id, stop id) once the side is claimed for a close of `kind`
    ("timed", or "close": one you asked for): its row "firing", the side's armed
    target and its working or pending stop marked cancelled for it (before any cancel
    is sent, so neither the watch nor the monitor acts on them). None if the side is
    closed or a close is already running on it, or, for a timed close, its exits are
    off (no longer: bots slice 18, the timed close runs with exits off, as Tracker2's).
    `contracts`: the close's size in group units (default: the group's open units,
    group_units). Any group with legs can be claimed; the row records them per unit.
    Called with EXITS_LOCK held."""
    event = _PULLED_BY[kind]
    now = deps.now()
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or run.status not in ("filled", "partial"):
            return None
        own, _ = exit_math.group_legs(run.legs, position)
        if not own:
            return None
        side = list(
            session.scalars(
                select(BotExit).where(BotExit.run_id == run_id, BotExit.group_position == position)
            )
        )
        if timing.side_state((r.kind, r.state) for r in side) != "open":
            return None
        if held_back(side):
            return None  # an earlier close may still be live: settle_held lifts it
        size = contracts if contracts is not None else group_units(session, run, position)
        row = BotExit(
            run_id=run_id, group_position=position, kind=kind, state="firing",
            **group_columns(session, run, position, side),
            contracts=size, rearms=0,
            log=[{"at": now.isoformat(), "event": note, **logged}],
            created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(row)
        for target in side:
            if target.kind == "target" and target.state == "armed":
                target.state = "cancelled"
                _breaches.pop(target.id, None)
                _log(target, deps, event=event)
            elif target.kind == "target" and target.state in ("working", "pending"):
                # Decision 0048: a resting target is pulled the same way, marked before its
                # cancel goes out; close_group cancels it and reads it back before sizing.
                target.state = "cancelled"
                _log(target, deps, event=event, resting=True)
        stop = next((r for r in side if r.kind == "stop" and r.state in STOP_LIVE), None)
        if stop is not None:
            if stop.state in ("pending", WATCHING):
                stop.broker_order_id = None  # never placed: any id on it is an old, dead order
            stop.state = "cancelled"  # before the cancel, so the monitor won't re-arm it
            _log(stop, deps, event=event)
        session.commit()
        return row.id, run.bot_id, (stop.id if stop else None)


def _target_job(deps, exit_id: int, run_id: int, bot_id: int, stop_id: int | None) -> None:
    """The background close of one side for its profit target. Never raises."""
    progress = {"cancel_sent": False}
    try:
        close_group(deps, exit_id, run_id, bot_id, stop_id, progress)
    except Exception:  # noqa: BLE001 -- never leave a side unprotected and silent
        logger.exception("profit target %s failed part-way", exit_id)
        with EXITS_LOCK:
            _recover(
                deps, exit_id, bot_id, stop_id, progress["cancel_sent"],
                live_order=progress.get("live_order"),
            )  # fmt: skip
    finally:
        FIRING.discard(exit_id)


def _claim_target(deps, exit_id: int):
    """(run id, bot id, stop id) once the target is marked firing and its stop
    cancelled for it, or None if it can't fire. Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        target = session.get(BotExit, exit_id)
        if target.state != "armed":
            return None
        if target_off(session, target.run_id, target.group_position):
            target.state = "cancelled"  # you switched this side's target off
            _breaches.pop(exit_id, None)
            _log(target, deps, event=EXITS_OFF)
            session.commit()
            return None
        side_stops = list(
            session.scalars(
                select(BotExit).where(
                    BotExit.run_id == target.run_id,
                    BotExit.group_position == target.group_position,
                    BotExit.kind == "stop",
                )
            )
        )
        if any(s.state == "filled" for s in side_stops):
            target.state = "cancelled"  # the stop already took this side
            _log(target, deps, event="the side's stop filled")
            session.commit()
            return None
        target.state = "firing"
        _log(target, deps, event="target reached")
        # A stop held by Freeze ("pending") is held back too, so the monitor never
        # places it while its side is closing.
        stop = next((s for s in side_stops if s.state in STOP_LIVE), None)
        if stop is not None:
            if stop.state in ("pending", WATCHING):
                stop.broker_order_id = None  # never placed: any id on it is an old, dead order
            stop.state = "cancelled"  # before the cancel, so the monitor won't re-arm it
            _log(stop, deps, event=CANCELLED_FOR_TARGET)
        session.commit()
        run_id, bot_id = target.run_id, session.get(BotRun, target.run_id).bot_id
        return run_id, bot_id, (stop.id if stop else None)


def _recover(deps, exit_id: int, bot_id: int, stop_id: int | None, cancel_sent: bool,
             why: str = "error while firing", live_order: str | None = None) -> None:  # fmt: skip
    """A target blew up (or a restart cut it off) after the stop was marked
    cancelled. First the close's own order, if it recorded one: cancelled and
    confirmed before anything else (never a stop on top of a live close); if it
    filled, the side is closed. Then fail the target and make sure the side is
    protected: un-mark the stop if its cancel was never sent, or if the broker
    still shows it (or shows it filled, for the monitor to settle); re-place it
    only when the broker confirms the old one is gone. `live_order` is a market
    buy-back the job sent but couldn't record: it may be live, so nothing is put
    back and the alert says so."""
    name, outcome, kind, restored, by = "A bot", "no_stop", "target", "", None
    try:
        name = bot_store.load(deps.session_factory, bot_id).name
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            kind, by = row.kind, _by_of(row.log)
    except Exception:  # noqa: BLE001 -- the recovery and its alert go on without the name
        logger.exception("couldn't load bot %s to recover target %s", bot_id, exit_id)
    try:
        if live_order:
            outcome = "market_live"
            with deps.session_factory() as session:
                target = session.get(BotExit, exit_id)
                target.state = "failed"
                _log(target, deps, event=why, market_order=live_order)
                session.commit()
            raise _Settled
        seen: dict = {}
        closing = _settle_close_order(deps, exit_id, seen)
        if closing in ("none", "gone") and _pulled_target_filled(deps, exit_id):
            _alert(deps, f"{name}: profit target filled", "A close cut off by a restart had "
                   "pulled the profit target, which had filled: that group is closed.", bot_id,
                   routine=True)  # fmt: skip
            return
        if closing != "live":
            with deps.session_factory() as session:
                row = session.get(BotExit, exit_id)
                part = _leg_out_left(
                    row.log, exit_legs(row), seen.get("legs_filled", {}), closing == "filled"
                )
            if part:  # a part-done leg-out: nothing put back
                _recovered_part(deps, exit_id, bot_id, name, why, kind, part)
                return
        if closing == "filled" and kind == "close":
            _recovered_close(deps, exit_id, bot_id, stop_id, name, why)
            return
        if closing == "filled":
            _recovered_filled(deps, exit_id, bot_id, name, why, kind)
            return
        left = None
        with deps.session_factory() as session:
            target = session.get(BotExit, exit_id)
            target.state = "failed"
            done = _confirmed_done(target.log, seen) if kind == "close" else 0
            if done > 0 and closing != "live":
                # What it closed before it was cut off: recorded, so the side's open size
                # (group_units) and its chip don't overstate what's left.
                target.state = "reduced"
                start = next((e["open_at_start"] for e in target.log if "open_at_start" in e), None)
                left = None if start is None else max(start - done, 0)
                if left is not None and stop_id is not None:
                    stop = session.get(BotExit, stop_id)
                    stop.contracts = min(stop.contracts, left)
                _log(target, deps, event=why, close_order=closing, closed=done)
            else:
                _log(target, deps, event=why, close_order=closing)
            session.commit()
        if closing == "live":
            outcome = "close_live"
        else:
            # Nothing of the close is live. Decision 0048: the target it pulled is settled
            # first (a resting one read back), so a target that may still rest never has
            # its stop watched again: a close that stop fired would be a second order.
            if kind in _PULLED_BY:
                restored = _restore_target(deps, exit_id, left)
            if _side_target_may_rest(deps, exit_id):
                outcome = "target_unclear"
            elif stop_id is not None:
                outcome = _settle_old_stop(deps, stop_id, cancel_sent)
    except _Settled:
        pass
    except Exception:  # noqa: BLE001 -- the alert below still goes out
        logger.exception("couldn't recover profit target %s", exit_id)
        if outcome != "market_live":
            outcome = "unknown"
    no_stop = " That side may have no stop."
    tail = {
        "close_live": " The close may still be working, so the stop wasn't put back." + no_stop,
        "no_stop": no_stop,
        "back": " The stop is back.",
        "still_there": " The stop is still in place.",
        "pending": " Its stop was never placed (Freeze held it); it will be placed at the "
        "next check. That side has no stop until then.",
        "filled": " The stop filled; its wing will be sold.",
        "unread": " The stop couldn't be read back; the monitor will re-read it." + no_stop,
        "unknown": no_stop,
        "none": no_stop,
        "market_live": " A market order buying back the short may still be working, so the "
        "stop wasn't put back and the wing wasn't sold." + no_stop,
        "target_unclear": " Its profit target couldn't be read back, so it may still rest at "
        "your broker: no close is sent on that side, and its stop isn't watched either." + no_stop,
    }[outcome]
    if kind in _PULLED_BY:
        again = f" Its profit target is {restored}." if restored else ""
        what = "a timed close" if kind == "timed" else f"a {_close_noun(by)}"
        _alert(
            deps,
            f"{name}: {_WHAT[kind]} error",
            f"Something went wrong with {what}; it was stopped and the side may still "
            f"be open.{tail}{again} Check your broker.",
            bot_id,
        )
        return
    _alert(
        deps,
        f"{name}: profit target error",
        f"Something went wrong closing a side for its profit target; the target was dropped."
        f"{tail} Check your broker.",
        bot_id,
    )


class _Settled(Exception):
    """_recover's early way out: the outcome is already decided."""


def _confirmed_done(log, seen: dict) -> int:
    """The units a close you asked for is known to have closed: the most its job
    logged (`done`), or its last order's own whole units, whichever is more."""
    logged = max((int(e.get("done", 0)) for e in log or ()), default=0)
    return max(logged, int(seen.get("units_filled", 0)))


def _settle_close_order(deps, exit_id: int, seen: dict | None = None) -> str:
    """The target's recorded close order, settled: "none" (it never sent one),
    "gone" (dead at the broker, now or after a confirmed cancel), "filled", or
    "live" (still working after the cancel, or unreadable: it may be). `seen` gets
    the whole group units the order filled (`units_filled`, trading.orders.filled_units),
    for a close you asked for."""
    with deps.session_factory() as session:
        target = session.get(BotExit, exit_id)
        run_id, order_id = target.run_id, target.broker_order_id
    if not order_id:
        return "none"
    run, bot = _run_and_bot(deps, run_id)
    try:
        client = _broker(deps, run)
        found = client.get_order(run.account, order_id)
        if found.may_be_working:
            orders.cancel(
                deps.session_factory, deps.clients(), run.broker, run.account, order_id,
                "bot", bot.id, now=deps.now(), notify=deps.notify,
            )  # fmt: skip
            found = read_after_cancel(deps, lambda: client.get_order(run.account, order_id))
    except Exception:  # noqa: BLE001 -- can't tell: it may be live
        logger.exception("couldn't settle close order %s", order_id)
        return "live"
    if seen is not None:
        with deps.session_factory() as session:
            legs = exit_legs(session.get(BotExit, exit_id))
        ratios = {plain(leg.symbol): leg.ratio for leg in legs}
        mine = [leg for leg in found.legs if plain(leg.symbol) in ratios]
        seen["units_filled"] = (
            filled_units(replace(found, legs=tuple(mine)), ratios)[0] if mine else 0
        )
        seen["legs_filled"] = {plain(leg.symbol): leg.quantity - leg.remaining for leg in mine}
    if found.status is OrderStatus.FILLED:
        return "filled"
    return "live" if found.may_be_working else "gone"


def _leg_out_left(log, legs, last_fills: dict, last_filled: bool = False) -> list:
    """The legs a leg-out cut off part-way left open, from its log: every sell leg not
    logged bought back, and every buy leg not handed to its own row. Empty if it
    hadn't legged out, if nothing of it filled yet (the group is as it was: the usual
    recovery puts the stop back), or if it had finished (every leg proven or handed).
    `last_fills`: what the close's last recorded order filled, by plain symbol;
    `last_filled`: that order filled in full (its sell legs are bought back)."""
    entries = [e for e in log or () if isinstance(e, dict) and e.get("stage") == "leg_out"]
    if not entries:
        return []
    bought = {plain(e["bought_back"]) for e in entries if "bought_back" in e}
    if last_filled:
        bought |= set(last_fills)
    handed = {plain(e["handed"]) for e in entries if "handed" in e}
    selling = next((e["selling"] for e in reversed(entries) if "selling" in e), None)
    touched = (
        bought
        or handed
        or any(last_fills.values())
        or any(e.get("status") in ("filled", "partial") for e in entries)
    )
    if not touched:
        return []
    left = [leg for leg in legs if leg.side is Side.SELL and plain(leg.symbol) not in bought]
    buys = [leg for leg in legs if leg.side is Side.BUY]
    if selling is not None:
        wanted = {plain(symbol) for symbol in selling}
        buys = [leg for leg in buys if plain(leg.symbol) in wanted]
    return left + [leg for leg in buys if plain(leg.symbol) not in handed]


def _recovered_part(deps, exit_id: int, bot_id: int, name: str, why: str, kind: str,
                    open_legs: list) -> None:  # fmt: skip
    """A close cut off part-way through its leg-out, some of it filled: the group is
    left to you. Never "filled", and no stop or target is put back (it couldn't close
    what's left: its close would be refused)."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        row.state = "failed"
        _log(row, deps, event=f"{why}; left to you part-way through legging out")
        session.commit()
    _alert(
        deps,
        f"{name}: {_WHAT.get(kind, 'profit target')} error",
        "A close cut off part-way through legging out had already closed some of its "
        f"legs. {_names(open_legs)} may still be held and nothing protects "
        f"{'it' if len(open_legs) == 1 else 'them'}: no stop or target was put back. "
        "Check your broker.",
        bot_id,
    )


def _recovered_filled(deps, exit_id: int, bot_id: int, name: str, why: str,
                      kind: str = "target") -> None:  # fmt: skip
    """The close cut off part-way had filled: the side is closed; its stop stays
    cancelled. If it was legging out, every sell leg was proven bought back and each
    buy leg handed to its own row (_leg_out_left): those rows sell them."""
    with deps.session_factory() as session:
        target = session.get(BotExit, exit_id)
        target.state = "filled"
        legging = any(e.get("stage") == "leg_out" for e in target.log)
        _log(target, deps, event=f"{why}; its close had filled")
        session.commit()
        legs = exit_legs(target)
    detail = "A close cut off part-way had filled; that group is closed."
    if legging:
        buys = [leg for leg in legs if leg.side is Side.BUY]
        rest = (
            f" {_names(buys)} {_is(buys)} sold on {'its' if len(buys) == 1 else 'their'} own."
            if buys
            else ""
        )
        detail = (
            "A close cut off while legging out had bought back every sold leg."
            f"{rest} Check your broker."
        )
    # Routine unless it was legging out (its words send it everywhere: check your broker)
    _alert(deps, f"{name}: {_WHAT.get(kind, 'profit target')} filled", detail, bot_id, routine=True)


def _recovered_close(deps, exit_id, bot_id, stop_id, name: str, why: str) -> None:
    """A close you asked for, cut off part-way, whose order had filled. What it was
    asked to close is closed; anything the side still has open is protected again,
    sized from the app's records and capped by what the broker shows (rearm)."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        start = next((e["open_at_start"] for e in row.log if "open_at_start" in e), None)
        done = row.contracts
        left = None if start is None else start - done
        state = "filled" if left is not None and left <= 0 else "reduced"
        row.state = state
        _log(row, deps, event=f"{why}; its close had filled", closed=done)
        session.commit()
        symbols = ", ".join(leg.symbol.strip() for leg in exit_legs(row))
    if state == "filled":
        _alert(deps, f"{name}: close filled", "A close cut off part-way had filled; that group "
               "is closed.", bot_id, routine=True)  # fmt: skip
        return
    again = _restore_target(deps, exit_id, left)  # I1: the target first, then the stop
    held = _side_target_may_rest(deps, exit_id)
    put_back = False if held else _protect_rest(deps, stop_id, left)
    stop = "its stop is back for what's left" if put_back else "its stop couldn't be put back"
    if again:
        stop += f", and its profit target is {again}"
    _alert(
        deps,
        f"{name}: close filled",
        f"A close cut off part-way had closed {done} of the group with {symbols}; {stop}. "
        "Check your broker.",
        bot_id,
    )


def _settle_old_stop(deps, stop_id: int, cancel_sent: bool) -> str:
    """Put a stop the close claimed back. One that was never placed (Freeze held it:
    no order id) goes back to "pending", for the monitor to place; never "working",
    which would leave the side with no stop for good."""

    def put_back_to_working(note: str) -> None:
        with deps.session_factory() as session:
            stop = session.get(BotExit, stop_id)
            stop.state = "working"  # the monitor re-reads it
            _log(stop, deps, event=note)
            session.commit()

    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        run_id, order_id = stop.run_id, stop.broker_order_id
        # A stop the app holds (slice 13's group on its P&L, decision 0048's on its short, or
        # a Schwab stop from before decision 0060): watched again, sized to what's held;
        # nothing was ever sent for it.
        if stop.watched or stop.app_held:
            session.rollback()
            return "back" if rearm(deps, stop_id, first=True) else "none"
        if not order_id:
            stop.state = "pending"
            _log(stop, deps, event="never placed; back to waiting to be placed")
            session.commit()
            return "pending"
    if not cancel_sent:
        put_back_to_working("the cancel was never sent; still working")
        return "still_there"
    run, _ = _run_and_bot(deps, run_id)
    try:
        found = _broker(deps, run).get_order(run.account, order_id)
    except Exception:  # noqa: BLE001 -- can't tell: leave it to the monitor
        logger.exception("couldn't read the old stop")
        put_back_to_working("couldn't read the stop after the error; the monitor will re-read")
        return "unread"
    if found.status is OrderStatus.FILLED:
        put_back_to_working("filled during the error; the monitor will close the rest of the group")
        return "filled"
    if found.may_be_working:
        put_back_to_working("the cancel didn't land; still working")
        return "still_there"
    return "back" if rearm(deps, stop_id) else "none"


def close_group(deps, exit_id, run_id, bot_id, stop_id, progress) -> None:
    """Close one group for its target, a timed close or a close you (or a rule) asked
    for (its row's kind; close_jobs.begin_close, exit_chase): cancel the stop and
    confirm; a timed close or one asked for sizes every leg to what this run holds
    (_size_close), a target closes the units it was armed for at its own value; then
    one closing limit over every leg, reversed, chased one step at a time (_chase_step).
    A close that pays starts at the mid and pays more; one that receives starts at the
    mid and asks less, never under one tick. Never at market as a combo."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        legs, kind, position = exit_legs(row), row.kind, row.group_position
        units, limit, by = row.contracts, row.limit, _by_of(row.log)
        market = bool(row.log) and row.log[0].get("order_type") == MARKET  # bots slice 18
        raw_floor = row.log[0].get("floor") if row.log else None  # bots slice 21
        floor = Decimal(raw_floor) if raw_floor else None
        app_watched = bool(row.watched)
        held_short = bool(row.app_held)
        stop_order = session.get(BotExit, stop_id).broker_order_id if stop_id else None
        run_row = session.get(BotRun, run_id)
        entry, side_sign = group_entry_of(run_row, legs), _side_sign(run_row)
    run, bot = _run_and_bot(deps, run_id)
    name = next((g.name for g in bot.groups if g.position == position), None)
    close = _Close(exit_id, stop_id, run, bot, legs, units, entry, progress=progress, kind=kind,
                   by=by, group=name or f"Group {position + 1}", watched=app_watched,
                   app_held=held_short, side_sign=side_sign)  # fmt: skip
    if stop_order:
        progress["cancel_sent"] = True
        cancelled = orders.cancel(
            deps.session_factory,
            deps.clients(),
            run.broker,
            run.account,
            stop_order,
            "bot",
            bot.id,
            now=deps.now(),
            notify=deps.notify,
        )
        if cancelled.action == "refuse":
            # Never sent (Freeze, or the broker isn't connected): the stop still stands.
            progress["cancel_sent"] = False
            _stop_stands(deps, close, f"stop cancel not sent: {cancelled.message}")
            if kind in _PULLED_BY:
                return _timed_not_done(
                    deps, close, f"the stop's cancel wasn't sent ({cancelled.message})"
                )
            _alert(
                deps,
                f"{bot.name}: profit target dropped",
                f"The stop's cancel wasn't sent ({cancelled.message}), so the profit target was "
                f"dropped. The stop on {close.name} is still working.",
                bot.id,
                run_id=run_id,
            )
            return
        # Sent, or unclear: the broker's answer says how the stop stands.
        found = _broker(deps, run).get_order(run.account, stop_order)
        if found.status is OrderStatus.FILLED or found.may_be_working:
            filled = found.status is OrderStatus.FILLED
            _stop_stands(
                deps, close, "the stop filled first" if filled else "stop cancel not confirmed"
            )
            if kind in _PULLED_BY:
                if filled:
                    return  # the group is closing by its stop: its target stays off
                return _timed_not_done(deps, close, "the stop's cancel wasn't confirmed")
            if not filled:
                _alert(
                    deps,
                    f"{bot.name}: profit target dropped",
                    "The stop's cancel wasn't confirmed, so the profit target was dropped. "
                    f"The stop on {close.name} is still working.",
                    bot.id,
                    run_id=run_id,
                )
            return
    with deps.session_factory() as session:
        pulled = _pulled_resting(session, run_id, position)
    if pulled is not None and _cancel_pulled_target(deps, close, pulled) is not None:
        return  # held, or the target took the group: the close never starts
    if kind in _PULLED_BY:
        # Sized to what's held, then priced from the mid (no mid: the natural). A target
        # closes the units it was armed for, as slice 3's did; its leg-out re-reads what's
        # held before it sends a leg on its own.
        if not _size_close(deps, close):
            return
        if not _leave_bidless_longs(deps, close):
            return
        legs = close.legs
        if market and len(legs) == 1:
            return _market_close(deps, close)  # manual_close._settings: straight to market
        price = _first_price(deps, close)
        if price is None:
            return
        if floor is not None:
            price = max(price, floor)
    else:
        if not _leave_bidless_longs(deps, close):
            return
        legs = close.legs
        price = exit_math.close_price(legs, limit, close.sign)  # the target's own value
    if _asked(deps, exit_id):  # Cancel was pressed before anything was sent
        return _cancelled(deps, close)
    order = Order(_closing_legs(legs), close.units, False, price=price)

    def should_stop() -> bool:
        # A timed close (or one you asked for) never chases past the session's close
        # (exit_chase's market_is_closed); a target close keeps slice 3's chase as it was.
        return _asked(deps, exit_id) or (kind in _PULLED_BY and not _session_open(deps))

    step = _chase_step(order, legs)
    if floor is not None:  # never asks less than the limit you set: it waits there
        conceding = step

        def step(price, by):
            found = conceding(price, by)
            return None if found is None else max(found, floor)

    outcome = chase(
        deps, _request(bot, run, order), price, bot.exit_chase, step,
        on_order=lambda oid: _note_order(deps, exit_id, oid), should_stop=should_stop,
    )  # fmt: skip
    close.count(outcome.order)
    _stage(deps, close, "chase", status=outcome.status, chase=outcome.log)
    if outcome.status == "filled":
        return _closed(deps, close, price)
    if outcome.status == "unclear":
        return _unclear(deps, close, f"The close for {close.name} may still be working")
    if outcome.status == "stopped" and kind in _PULLED_BY and not _asked(deps, exit_id):
        return _give_up(deps, close, "the session ended")  # the stop goes back (or expires)
    if outcome.status == "stopped" or _asked(deps, exit_id):
        return _cancelled(deps, close)
    if floor is not None:  # a limit you set: never past it at market or by a leg-out
        return _give_up(deps, close, f"{outcome.status} at its {floor:f} limit")
    if (
        outcome.status in ("missed", "partial")
        and bot.exit_chase.switch_to_market
        and len(legs) == 1
        and not _must_close(close)
    ):
        # Bots slice 19 (exit_chase._Chase.go_market): a one-leg close goes to market for
        # what's left once its chase has cancelled and confirmed its own order. A bought
        # leg with no bid is never sold at market (rest_at_floor; question 10): the
        # close's ending runs as before, so nothing is left working.
        rest, why = _market_rest(deps, close)
        if why is None:
            return _market_close(deps, close, rest)
        _stage(deps, close, "market", event=f"not sent at market: {why}")
    if (
        outcome.status in ("missed", "partial")
        and bot.exit_chase.exhaust_mode == KEEP
        and not _must_close(close)  # a watched stop's close goes natural, then legs out
    ):
        return _keep_conceding(deps, close, _last_price(outcome.log, price))
    if outcome.status in ("missed", "partial") and (
        bot.exit_chase.exhaust_mode == NATURAL or _must_close(close)
    ):
        return _natural_then_leg_out(deps, close)
    _give_up(deps, close, outcome.status)


def _bidless_longs(deps, close: _Close) -> tuple[exit_math.GroupLeg, ...]:
    """The close's bought legs whose bid is None or 0 (_bid: no fresh bid above 0)."""
    return tuple(
        leg for leg in close.legs if leg.side is Side.BUY and _bid(deps, leg.symbol) is None
    )


def _leave_bidless_longs(deps, close: _Close) -> bool:
    """Ignore bid-less longs (close_jobs.select_close_legs, decision 0065): a bought leg
    nobody bids on is left out of the close, before its first price, and expires; the
    sold legs are always closed. Never on a close you started by hand
    (manual_close._settings) or a partial one (scale_out): those close every leg. A
    left leg stays open in the Journal until expiry settles it. Every leg left: nothing
    to send, so the close gives up and says why. False when it ended here."""
    if not close.bot.exit_chase.ignore_bidless_longs:
        return True
    if close.kind == "close" and (
        close.by is None or close.units < close.progress.get("open_at_start", close.units)
    ):
        return True  # started by hand, or a partial close: every leg is closed
    left = _bidless_longs(deps, close)
    if not left:
        return True
    named = ", ".join(leg.symbol.strip() for leg in left)
    _stage(deps, close, "left", left=[leg.symbol for leg in left], why="no bid")
    close.legs = tuple(leg for leg in close.legs if leg not in left)
    if not close.legs:
        _give_up(deps, close, f"every leg is a bought leg with no bid ({named})")
        return False
    return True


def _market_rest(deps, close: _Close) -> tuple[int, str | None]:
    """(units to send at market, None) after a one-leg close's chase (bots slice 19), or
    (0, why not). What's left of the close, capped at what the broker holds now the
    close's way (a target close was armed for its units, never sized to the position;
    _Chase.go_market's open_legs). A bought leg with no bid, unreadable positions or
    nothing held: no market order."""
    leg = close.legs[0]
    if leg.side is Side.BUY and _bid(deps, leg.symbol) is None:
        return 0, "the bought leg has no bid"
    try:
        held = _positions(deps, close.run).get(plain(leg.symbol), 0)
    except Exception:  # noqa: BLE001 -- never size a market order on a guess
        logger.exception("couldn't read positions before a market close")
        return 0, "couldn't read positions"
    way = -held if leg.side is Side.SELL else held
    rest = min(close.units - close.units_done(), max(way, 0) // leg.ratio)
    if rest <= 0:
        return 0, "nothing left held to close"
    return rest, None


def _market_close(deps, close: _Close, units: int | None = None) -> None:
    """A close you asked for at Market on a one-leg group (bots slice 18; Tracker2's
    manual_close._settings: no limit tries, then _Chase.go_market): one market order for
    what's held, in the regular session only, read back as the leg-out's market buy-back
    is. Filled: closed. Ended unfilled: what filled is kept and the rest protected again.
    Still working or unreadable: unclear, nothing more is sent and no stop is put back."""
    if _asked(deps, close.exit_id):
        return _cancelled(deps, close)
    if not _session_open(deps):
        return _give_up(deps, close, "the session is over, so nothing was sent at market")
    leg = close.legs[0]
    side = Side.BUY if leg.side is Side.SELL else Side.SELL
    rest = close.units if units is None else units  # slice 19: after a chase, what's left
    order = Order((Leg(leg.symbol, side),), rest * leg.ratio, False, OrderType.MARKET)
    result = orders.submit(
        deps.session_factory, deps.clients(), _request(close.bot, close.run, order),
        now=deps.now(), notify=deps.notify,
    )  # fmt: skip
    if _placed(result):
        close.progress["live_order"] = result.broker_order_id  # known before anything breaks
    _stage(deps, close, "market", contracts=order.quantity, result=result.message)
    if not _placed(result):
        if result.message.startswith(orders.UNCLEAR):
            return _unclear(deps, close, f"The market order closing {close.name} may be working")
        return _give_up(deps, close, f"the market order wasn't sent ({result.message})")
    note = lambda oid: _note_order(deps, close.exit_id, oid)  # noqa: E731
    if record_order(note, result.broker_order_id):
        return _unclear(deps, close, f"A market order closing {close.name} may be working (the "
                        "app couldn't record it)")  # fmt: skip
    close.progress.pop("live_order", None)  # on the close's row now, for recovery
    for _ in range(3):
        deps.sleep(close.bot.exit_chase.interval_seconds)
        try:
            found = _broker(deps, close.run).get_order(close.run.account, result.broker_order_id)
        except Exception:  # noqa: BLE001 -- it may be live
            logger.exception("couldn't read the close's market order")
            return _unclear(deps, close, f"The market order closing {close.name} couldn't be read")
        if found.status is OrderStatus.FILLED or not found.may_be_working:
            close.count(found)
            _stage(deps, close, "market", status=found.status.value)
            if found.status is OrderStatus.FILLED and close.units_done() >= close.units:
                return _closed(deps, close, None, " at market")
            return _give_up(deps, close, f"the market order ended {found.status}")
    return _unclear(deps, close, f"The market order closing {close.name} is still working")


def _closing_legs(legs) -> tuple[Leg, ...]:
    """Every leg of the group reversed, at its ratio per unit: a sell leg bought back,
    a buy leg sold."""
    return tuple(
        Leg(leg.symbol, Side.BUY if leg.side is Side.SELL else Side.SELL, leg.ratio) for leg in legs
    )


def _chase_step(order: Order, legs):
    """One concession of a group close (execution._concede): by the chase's step, or
    the order's tick if that's more (a step under the tick would round back to the
    price already out); a receiving close stops at one tick (next_close_price)."""

    def step(price: Decimal, by: Decimal) -> Decimal | None:
        return exit_math.next_close_price(legs, price, max(by, ticks.order_tick(order, price, {})))

    return step


def group_entry_of(run_row: BotRun, legs) -> Decimal | None:
    """The group's entry per unit from the run's fills (exit_math.group_entry), or None
    while any leg has no fill price."""
    fills = {
        f["symbol"]: Decimal(f["fill_price"])
        for f in run_row.filled_legs or ()
        if f.get("fill_price") is not None
    }
    return exit_math.group_entry(legs, fills)


def _side_sign(run_row: BotRun) -> Decimal:
    """The run's side as a sign, for a close with no entry price (_Close.side_sign): -1 when
    it filled (else asked) for a debit, else 1."""
    side = run_row.fill_credit if run_row.fill_credit is not None else run_row.target_credit
    return Decimal(-1) if side is not None and side < 0 else Decimal(1)


def _must_close(close: _Close) -> bool:
    """A watched stop's close (slice 11; slice 13 on any broker for a group the app
    watches on its P&L): it goes on to the natural price and legs out, ending with the
    sold legs at market, whatever the bot's exit chase's ending."""
    return (
        close.kind == "close" and close.by == WATCHED_STOP_BY and (close.watched or close.app_held)
    )


def _stop_stands(deps, close: _Close, event: str) -> None:
    """The stop's cancel wasn't sent, wasn't confirmed, or the stop filled first: the
    close is dropped and the stop goes back to working (the monitor settles it)."""
    with deps.session_factory() as session:
        row, stop = session.get(BotExit, close.exit_id), session.get(BotExit, close.stop_id)
        row.state = "cancelled" if close.kind == "target" else "failed"
        _log(row, deps, event=event)
        stop.state = "working"
        session.commit()


def _timed_not_done(deps, close: _Close, why: str) -> None:
    """A timed close (or one you asked for) that never sent its close: the target goes
    back and it alerts."""
    again = _restore_target(deps, close.exit_id)
    again = f" Its profit target is {again}." if again else ""
    what = "timed close" if close.kind == "timed" else close.noun
    _alert(
        deps,
        f"{close.bot.name}: {close.what} not done",
        f"The {what} for {close.name} didn't run: {why}. The stop is still "
        f"working and the group is still open.{again} Check your broker.",
        close.bot.id,
        run_id=close.run.id,
    )


def _size_close(deps, close: _Close) -> bool:
    """Size the close to what this run holds (close_jobs.select_close_legs): each leg's
    holding at the broker, in the close's direction, in whole units of its ratio; the
    close is the fewest of those, capped at its own units. A leg held the wrong way (a
    buy-to-close where the account is long, a sell where it's short) refuses the whole
    close: it would open or grow a position. Some legs gone while others are held is
    never read as closed: nothing is sent and it alerts. Nothing held at all ends it
    "expired". No position read gives up (the stop goes back). False when it ended
    here."""
    try:
        held = _positions(deps, close.run)
    except Exception:  # noqa: BLE001 -- never size a close on a guess
        logger.exception("couldn't read positions to size a close")
        _give_up(deps, close, "couldn't read positions to size it")
        return False
    # Signed so that what this close can close counts up: a sell leg is held short.
    way = {leg: held.get(plain(leg.symbol), 0) * (-1 if leg.side is Side.SELL else 1)
           for leg in close.legs}  # fmt: skip
    wrong = [leg for leg in close.legs if way[leg] < 0]
    if wrong:
        named = ", ".join(leg.symbol.strip() for leg in wrong)
        _refused(
            deps, close, way, f"{named} is held the wrong way; nothing sent",
            f"{named} is held the wrong way for this close (it would open or add to a "
            f"position), so nothing was sent for {close.name}.",
        )  # fmt: skip
        return False
    held_units = min(way[leg] // leg.ratio for leg in close.legs)
    if held_units <= 0:
        still = [leg for leg in close.legs if way[leg] > 0]
        if still:
            # Some legs are gone and others aren't: never call that group closed quietly.
            gone = ", ".join(leg.symbol.strip() for leg in close.legs if leg not in still)
            kept = ", ".join(leg.symbol.strip() for leg in still)
            _refused(
                deps, close, way, f"{gone} isn't held but {kept} is; nothing sent",
                f"{gone} of {close.name} is no longer held but {kept} still is (the "
                "position was changed outside this close), so nothing was sent and nothing "
                "was sold.",
            )  # fmt: skip
            return False
        _end(deps, close, "expired", "nothing held: the group is already closed")
        if close.kind == "close":
            _alert(deps, f"{close.bot.name}: close not done", f"Nothing of {close.name} is "
                   "held any more, so nothing was sent.", close.bot.id,
                   run_id=close.run.id)  # fmt: skip
        return False
    extra = {}
    if close.kind == "close":
        # What the group has open now (the broker's count, capped at the app's): what's
        # left after this close is protected again from it (_close_filled).
        with deps.session_factory() as session:
            row = session.get(BotExit, close.exit_id)
            open_size = next((e["open"] for e in row.log if "open" in e), close.units)
        close.progress["open_at_start"] = min(held_units, open_size)
        extra["open_at_start"] = close.progress["open_at_start"]
    size = min(held_units, close.units)
    if size != close.units or extra:
        close.units = size
        with deps.session_factory() as session:
            row = session.get(BotExit, close.exit_id)
            row.contracts = size
            _log(row, deps, stage="sized", contracts=size, **extra)
            session.commit()
    return True


def _refused(deps, close: _Close, way: dict, event: str, said: str) -> None:
    """A close _size_close refused after its stop was pulled: nothing was sent, so the
    group's protection goes back as a give-up puts it back (the stop, and a timed
    close's target), then it alerts with what it refused and how the stop stands."""
    _end(deps, close, "failed", event)
    back, again = _target_then_stop(deps, close, None, lambda: _put_stop_back(deps, close, way))
    if back is None:
        tail = ""
    elif back:
        tail = " Its stop is back."
    else:
        tail = f" Its stop couldn't be put back, so {close.name} may have no stop."
    tail += f" Its profit target is {again}." if again else ""
    _alert(
        deps,
        f"{close.bot.name}: {close.what} not done",
        f"{said}{tail} Check your broker.",
        close.bot.id,
        run_id=close.run.id,
    )


def _put_stop_back(deps, close: _Close, way: dict) -> bool | None:
    """The stop this close pulled, put back (rearm, not counted as a re-arm): a stop
    the app watches is watched again; a resting stop only while its short is still
    held short (a buy stop on a long holding would add to it). None with no stop."""
    if close.stop_id is None:
        return None
    with deps.session_factory() as session:
        stop = session.get(BotExit, close.stop_id)
        app_watched, anchor = bool(stop.watched), stop.anchor_symbol or stop.short_symbol
    if not app_watched:
        leg = next((leg for leg in close.legs if leg.symbol == anchor), None)
        if leg is None or leg.side is not Side.SELL or way[leg] <= 0:
            return False
    with EXITS_LOCK:
        return rearm(deps, close.stop_id, first=True)


def _first_price(deps, close: _Close) -> Decimal | None:
    """A timed close's (or one you asked for) first price (exit_chase._Chase._run):
    the group's mid conceded by the start offset; no mid: the natural. No fresh quote
    gives up (the stop goes back). None when it ended here."""
    price, priced = _mid_close(deps, close), "mid"
    if price is None:
        price, priced = _natural(deps, close), "natural"
    if price is None:
        _give_up(deps, close, "no fresh quotes to price it")
        return None
    with deps.session_factory() as session:
        row = session.get(BotExit, close.exit_id)
        _log(row, deps, stage=close.kind, contracts=close.units, start=f"{price:f}",
             priced=priced)  # fmt: skip
        session.commit()
    return price


def _fresh_quotes(deps, close: _Close) -> dict:
    """Each leg's fresh quote, or None (_quote)."""
    return {leg.symbol: _quote(deps, leg.symbol) for leg in close.legs}


def _mid_close(deps, close: _Close) -> Decimal | None:
    """The group's close at its mid conceded by the start offset, from fresh quotes on
    every leg (exit_math.group_mid_close_price)."""
    quotes = _fresh_quotes(deps, close)
    if any(q is None for q in quotes.values()):
        return None
    return exit_math.group_mid_close_price(
        close.legs, quotes, close.bot.exit_chase.start_offset, close.sign
    )


ARMED_AGAIN = "armed again"


def _restore_target(deps, exit_id: int, open_now: int | None = None) -> str:
    """A close that didn't close its side puts back the target it pulled (close_jobs._restore),
    sized to what's still open; never on a side that's closed, nor while another close runs
    on it (final review I1: never a target beside a running close). An armed target is armed
    again; decision 0048: a resting one is read back and placed again at TastyTrade
    (_put_resting_back). What became of it, in words ("Its profit target is ..."), or ""
    when no target is back."""
    with EXITS_LOCK:
        with deps.session_factory() as session:
            row = session.get(BotExit, exit_id)
            if row.kind not in _PULLED_BY:
                return ""
            side = list(session.scalars(select(BotExit).where(
                BotExit.run_id == row.run_id,
                BotExit.group_position == row.group_position)))  # fmt: skip
            if timing.side_state((r.kind, r.state) for r in side) in ("closed", "busy"):
                return ""
            if open_now is not None and open_now <= 0:
                return ""
            # You switched this side's target off: an armed target stays off; a resting one
            # is still read back (it may rest at the broker) and cancelled, never placed.
            off = target_off(session, row.run_id, row.group_position)
            what = _WHAT[row.kind]
            restored, resting = "", []
            for target in side:
                last = target.log[-1] if target.log else {}
                if not (target.kind == "target" and target.state == "cancelled"
                        and last.get("event") == _PULLED_BY[row.kind]):  # fmt: skip
                    continue
                if last.get("resting"):
                    resting.append(target.id)
                    continue
                if off:
                    continue
                target.state = "armed"
                if open_now is not None:
                    target.contracts = min(target.contracts, open_now)
                _log(target, deps, event=f"armed again: the {what} didn't close the side")
                restored = ARMED_AGAIN
            session.commit()
        for target_id in resting:
            restored = _put_resting_back(deps, target_id, open_now, what, off) or restored
        return restored


def _target_then_stop(deps, close: _Close, left: int | None, put_stop) -> tuple:
    """(how the stop stands, what became of the target) once a close ends without closing
    its group. Final review I1, as _recover: the target it pulled goes back first, then the
    stop (`put_stop`), so a stop already past its level can't claim a close that misses a
    target placed again after it. A target that may still rest (held) keeps the stop off:
    a close that stop fired would be a second closing order. The stop: None with none, else
    whether it's back."""
    again = _restore_target(deps, close.exit_id, left)
    if _side_target_may_rest(deps, close.exit_id):
        return (None if close.stop_id is None else False), again
    return put_stop(), again


def _pulled_resting(session, run_id: int, position: int) -> int | None:
    """The group's resting target a claimed close pulled and hasn't yet seen cancelled at the
    broker, or None."""
    found = [
        r.id
        for r in session.scalars(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.group_position == position,
            BotExit.kind == "target", BotExit.state == "cancelled"))
        if r.log and r.log[-1].get("resting") and r.log[-1].get("event") in CLOSE_MARKERS
        and r.log[-1].get("cancel") is None
    ]  # fmt: skip
    return max(found, default=None)


def _record_target_fill(session, deps, row: BotExit, found, event: str) -> None:
    """A resting target the broker shows filled: recorded, and the group's watch ended in
    the same session, so the caller's one commit writes both (decision 0048)."""
    row.state = "filled"
    row.fill_price = next((leg.fill_price for leg in found.legs if leg.fill_price is not None),
                          None)  # fmt: skip
    _log(row, deps, event=event, price=str(row.fill_price))
    _end_the_watch(session, deps, row.run_id, row.group_position, "the profit target filled")


def _starting(close: _Close) -> str:
    return "timed close" if close.kind == "timed" else close.noun


def _cancel_pulled_target(deps, close: _Close, target_id: int) -> str | None:
    """Decision 0048: a close of a group whose profit target rests at TastyTrade first
    cancels that target and reads the cancel back (Tracker2's management._cancel_working and
    _settle_cancel; the handshake a reached target uses with a resting stop, the other way
    round). None once it's gone: the close goes on, sized to what's held, so a part the
    target filled is already gone. "filled": the target took the group, recorded, nothing
    else sent. "held": the cancel wasn't sent, wasn't confirmed or can't be read back; the
    close doesn't start, the target is left as it is and you're alerted."""
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        order_id, legs = row.broker_order_id, exit_legs(row)
    if not order_id:
        return None  # Freeze held it back: nothing rests at the broker
    run = close.run
    sent = orders.cancel(
        deps.session_factory, deps.clients(), run.broker, run.account, order_id, "bot",
        close.bot.id, now=deps.now(), notify=deps.notify,
    )  # fmt: skip
    if sent.action == "refuse":
        return _target_stands(deps, close, target_id, f"its cancel wasn't sent ({sent.message})")
    try:
        found = read_after_cancel(deps, lambda: _broker(deps, run).get_order(run.account, order_id))
    except Exception:  # noqa: BLE001 -- can't tell whether it rests: held
        logger.exception("couldn't read run %s's target back after its cancel", run.id)
        return _target_unclear(deps, close, target_id, "couldn't be read back after its cancel")
    if found.status is OrderStatus.FILLED:
        return _target_took_it(deps, close, target_id, found)
    if found.may_be_working:
        return _target_stands(deps, close, target_id, "its cancel wasn't confirmed")
    filled, _ = filled_units(found, {plain(leg.symbol): leg.ratio for leg in legs})
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        event = row.log[-1].get("event") if row.log else None
        _log(row, deps, event=event, resting=True, cancel="confirmed", partly_filled=filled)
        session.commit()
    return None


def _target_stands(deps, close: _Close, target_id: int, why: str) -> str:
    """The resting target is still at the broker: it stays, the close never starts, the
    stop is watched again, and you're told."""
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        row.state = "working"
        _log(row, deps, event=f"still rests: {why}")
        session.commit()
    _end(deps, close, "failed", f"not started: the resting target stays ({why})")
    with EXITS_LOCK:
        back = rearm(deps, close.stop_id, first=True) if close.stop_id is not None else None
    stop = "" if back is None else (
        " Its stop is watched again." if back
        else f" Its stop couldn't be put back, so {close.name} may have no stop."
    )  # fmt: skip
    _alert(
        deps, f"{close.bot.name}: {close.what} not started",
        f"The {_starting(close)} for {close.name} didn't start. Its profit target rests at "
        f"{broker_name(close.run)} and was left as it is: {why}. A second closing order is "
        f"never sent on one group.{stop} Check your broker.", close.bot.id, run_id=close.run.id,
    )  # fmt: skip
    return "held"


def _target_unclear(deps, close: _Close, target_id: int, why: str) -> str:
    """Whether the target still rests can't be told: held back (Restore and changes refuse
    it), nothing sent, the run marked Needs you. Its stop stays off (a close it fired could
    be a second closing order), and you're told so."""
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        row.state = "failed"
        _log(row, deps, event=TARGET_UNCLEAR, why=why)
        session.commit()
    _end(deps, close, "failed", f"not started: the resting target {why}")
    detail = (
        f"The {_starting(close)} for {close.name} didn't start: its profit target resting at "
        f"{broker_name(close.run)} {why}, so a closing order may still rest there. Nothing "
        f"was sent. Its stop isn't watched either, so {close.name} may have no stop and no "
        "profit target until you check. Check your broker."
    )
    bot_entry.update_run(deps, close.run.id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    _alert(deps, f"{close.bot.name}: {close.what} not started", detail, close.bot.id,
           run_id=close.run.id)  # fmt: skip
    return "held"


def _target_took_it(deps, close: _Close, target_id: int, found) -> str:
    """The resting target filled before its cancel landed: the group is closed by it."""
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        _record_target_fill(session, deps, row, found, "filled")
        limit = row.limit
        session.commit()
    _end(deps, close, "cancelled", "not needed: the resting target filled first")
    _alert(
        deps, f"{close.bot.name}: profit target filled",
        f"{close.name} closed at its {limit} limit at {broker_name(close.run)} before the "
        f"{_starting(close)} started, so nothing else was sent.", close.bot.id,
        run_id=close.run.id, routine=True,
    )  # fmt: skip
    return "filled"


def _put_resting_back(deps, target_id: int, open_now: int | None, what: str,
                      off: bool = False) -> bool:  # fmt: skip
    """A resting target a close pulled, once that close is over without closing the group:
    still at the broker (its cancel never landed) it's working again; filled, it's recorded
    and the watch ends; gone, it's placed again sized to what's held. `off`: you switched
    the target off meanwhile, so one still at the broker is cancelled and read back, and
    none is placed. What became of it ("Its profit target is ..."), or "" when none rests
    again. Called with EXITS_LOCK held."""
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        run_id, position, order_id = row.run_id, row.group_position, row.broker_order_id
        units, limit, legs = row.contracts, row.limit, exit_math.row_legs(row)
        confirmed = bool(row.log) and row.log[-1].get("cancel") == "confirmed"
    run, bot = _run_and_bot(deps, run_id)
    if order_id and not confirmed:
        try:
            found = _broker(deps, run).get_order(run.account, order_id)
        except Exception:  # noqa: BLE001 -- can't tell: held, never a second target
            logger.exception("couldn't read target %s to put it back", target_id)
            _hold_target(deps, bot, run_id, position, target_id,
                         "couldn't be read back after a close that didn't run")  # fmt: skip
            return ""
        if found.may_be_working and off:
            _cancel_off_target(deps, bot, run, target_id, order_id)
            return ""
        if found.status is OrderStatus.FILLED or found.may_be_working:
            with deps.session_factory() as session:
                row = session.get(BotExit, target_id)
                if found.status is OrderStatus.FILLED:
                    _record_target_fill(session, deps, row, found,
                                        "filled while a close was pulling it")  # fmt: skip
                else:
                    row.state = "working"
                    _log(row, deps, event=f"still rests: the {what}'s cancel never landed")
                session.commit()
            if found.status is OrderStatus.FILLED:
                return ""
            return f"still resting at {broker_name(run)}"
    if off:
        _target_gone_off(deps, target_id)
        return ""
    size = units if open_now is None else min(units, open_now)
    size = _held_units_of(deps, run, legs, size)
    if size < 1:  # C2d: it stays cancelled, and you're told so
        with deps.session_factory() as session:
            _log(session.get(BotExit, target_id), deps, event="not placed again",
                 why="the broker doesn't show the group held")  # fmt: skip
            session.commit()
        _no_target_alert(
            deps, bot, run_id, position, "profit target not placed",
            f"{_group_name(bot, position)}: its profit target was cancelled for a {what} that "
            "didn't close the group, and it wasn't placed again: the broker doesn't show the "
            "whole group held.",
        )  # fmt: skip
        return ""
    shown = _legs_show(deps, run, {leg.symbol: size * leg.ratio for leg in legs})
    state, new_id, rests_at, message = _send_target(deps, bot, run, legs, size, limit, shown)
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        row.state, row.broker_order_id, row.contracts = state, new_id, size
        if rests_at is not None:
            row.limit = rests_at
        _log(row, deps, event=f"placed again: the {what} didn't close the group", result=message)
        session.commit()
    if sent_unclear(state, message):  # it may rest at TastyTrade: held, never a second
        _hold_unclear_send(deps, bot, run_id, position, target_id,
                           f"placing its profit target again after a {what}")  # fmt: skip
        return ""
    if state == "failed":
        _no_target_alert(
            deps, bot, run_id, position, "profit target not placed",
            f"{_group_name(bot, position)}: its profit target was cancelled for a {what} that "
            f"didn't close the group, and placing it again failed ({message}).",
            " Restore missing exits on the run page places it again.",
        )  # fmt: skip
    again = f"placed again at {broker_name(run)}"
    if state == "pending":
        return f"{again} once Freeze is off"
    return again if state == "working" else ""


TARGET_SWITCHED_OFF = "cancelled: you switched its target off"


def _target_gone_off(deps, target_id: int) -> None:
    """A pulled resting target seen gone at the broker while you have its target off: it
    stays cancelled, and that's recorded (nothing is placed)."""
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        _log(row, deps, event=TARGET_SWITCHED_OFF, cancel="confirmed")
        session.commit()


def _cancel_off_target(deps, bot, run: _Run, target_id: int, order_id: str) -> bool:
    """A pulled resting target still at the broker though you switched the target off (a
    close was cut off before its cancel): cancelled and read back, never placed again. If
    it can't be cancelled it's working again, so the next close pulls it, and you're told
    (that alert says it still rests). Always False: no target is put back."""
    with deps.session_factory() as session:
        position = session.get(BotExit, target_id).group_position
    sent = orders.cancel(
        deps.session_factory, deps.clients(), run.broker, run.account, order_id, "bot",
        bot.id, now=deps.now(), notify=deps.notify,
    )  # fmt: skip
    try:
        found = read_after_cancel(deps, lambda: _broker(deps, run).get_order(run.account, order_id))
    except Exception:  # noqa: BLE001 -- can't tell: held, never a second target
        logger.exception("couldn't read target %s back after its cancel", target_id)
        _hold_target(deps, bot, run.id, position, target_id,
                     "couldn't be read back after cancelling it for its target off")  # fmt: skip
        return False
    with deps.session_factory() as session:
        row = session.get(BotExit, target_id)
        if found.status is OrderStatus.FILLED:
            _record_target_fill(session, deps, row, found, "filled while its target was off")
        elif found.may_be_working:
            row.state = "working"
            _log(row, deps, event=f"still rests: its target is off but its cancel didn't land "
                 f"({sent.message})")  # fmt: skip
        else:
            _log(row, deps, event=TARGET_SWITCHED_OFF, cancel="confirmed")
        session.commit()
    if found.may_be_working:
        name = _group_name(bot, position)
        _alert(
            deps, f"{bot.name}: profit target still rests",
            f"{name}: you switched its profit target off, but cancelling it at {broker_name(run)} "
            "didn't work, so the switched-off target still rests there. Check your broker.",
            bot.id, run_id=run.id,
        )  # fmt: skip
    return False


def _side_target_may_rest(deps, exit_id: int) -> bool:
    """Whether the side of close `exit_id` has a profit target held as unclear
    (target_may_rest)."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        rows = session.scalars(select(BotExit).where(
            BotExit.run_id == row.run_id,
            BotExit.group_position == row.group_position))  # fmt: skip
        return target_may_rest(list(rows))


def _pulled_target_filled(deps, exit_id: int) -> bool:
    """Recovery (decision 0048): whether the resting target a cut-off close pulled had filled.
    If so it's recorded, the watch ends and the close is over: nothing is put back."""
    with deps.session_factory() as session:
        row = session.get(BotExit, exit_id)
        target_id = _pulled_resting(session, row.run_id, row.group_position)
        target = session.get(BotExit, target_id) if target_id is not None else None
        order_id = target.broker_order_id if target is not None else None
        run_id = row.run_id
    if not order_id:
        return False
    run, _ = _run_and_bot(deps, run_id)
    try:
        found = _broker(deps, run).get_order(run.account, order_id)
    except Exception:  # noqa: BLE001 -- unread: _put_resting_back holds it later
        logger.exception("couldn't read target %s in recovery", target_id)
        return False
    if found.status is not OrderStatus.FILLED:
        return False
    with deps.session_factory() as session:
        target = session.get(BotExit, target_id)
        _record_target_fill(session, deps, target, found,
                            "filled while a cut-off close was pulling it")  # fmt: skip
        close = session.get(BotExit, exit_id)
        close.state = "cancelled"
        _log(close, deps, event="not needed: the resting target had filled")
        session.commit()
    return True


NATURAL = "natural_then_leg_out"  # exit_chase.EXHAUST_NATURAL
KEEP = "keep_conceding"  # exit_chase.EXHAUST_KEEP


@dataclass
class _Close:
    """One group's close (for its target, a timed close or one asked for), and what
    the close's own orders filled (exit_chase.FillLedger: the only evidence a leg is
    closed). `units` is the close's size in group units; `entry` the group's entry
    per unit (+ a credit), None while a leg has no fill price."""

    exit_id: int
    stop_id: int | None
    run: _Run
    bot: object
    legs: tuple[exit_math.GroupLeg, ...]
    units: int
    entry: Decimal | None
    filled: dict = field(default_factory=dict)  # plain symbol -> contracts this close filled
    # The job's progress, shared with _target_job: "live_order" is a market buy-back
    # sent but not yet recorded on the target (recovery must treat it as live).
    progress: dict = field(default_factory=dict)
    kind: str = "target"  # the row closing the group: its "target", "timed" or "close"
    by: str | None = None  # slice 7: the rule that started a "close" (None: you)
    group: str = ""  # the group's name in the bot
    # Plain symbol -> contracts of a buy leg the leg-out handed to its own wing row once
    # every sell leg was proven bought back: closed as far as this close goes. Never a
    # leg that wasn't held or wasn't sold for the session's close (review M2).
    handed: dict = field(default_factory=dict)
    watched: bool = False  # the group's stop is the app's, on its P&L (slice 13)
    app_held: bool = False  # decision 0048: the stop is the app's on the short (TastyTrade)
    side_sign: Decimal = Decimal(1)  # the run's side: -1 a debit, read when `entry` is None

    @property
    def name(self) -> str:
        """The group's name, for alerts ("Put spread")."""
        return self.group or "the group"

    @property
    def what(self) -> str:
        return _WHAT[self.kind]

    @property
    def noun(self) -> str:
        """A "close" in words: one you asked for, or the rule's."""
        return _close_noun(self.by)

    @property
    def sign(self) -> Decimal:
        """The entry, or with no fill price a stand-in of the run's side (decision 0048):
        only a close valued at exactly 0 reads it (exit_math.receives)."""
        return self.entry if self.entry is not None else self.side_sign

    def count(self, order) -> None:
        """Count one order's fills, from its final read (each order once)."""
        for leg in order.legs if order is not None else ():
            key = plain(leg.symbol)
            self.filled[key] = self.filled.get(key, 0) + leg.quantity - leg.remaining

    def done(self, symbol: str) -> int:
        return self.filled.get(plain(symbol), 0)

    def units_done(self) -> int:
        """Whole units this close's own fills closed: the fewest over its legs."""
        return min(
            ((self.done(leg.symbol) + self.handed.get(plain(leg.symbol), 0)) // leg.ratio
             for leg in self.legs),
            default=0,
        )  # fmt: skip

    def even(self) -> bool:
        """Every leg filled exactly units_done x its ratio (no leg ran ahead)."""
        units = self.units_done()
        return all(self.done(leg.symbol) == units * leg.ratio for leg in self.legs)


def _close_noun(by: str | None) -> str:
    return f"close for the {by}" if by else "close you asked for"


def _by_of(log) -> str | None:
    """The rule that started a close, from its first log entry (slice 7)."""
    return next((e.get("by") for e in (log or ())[:1] if isinstance(e, dict)), None)


def _stage(deps, close: _Close, stage: str, **entry) -> None:
    if close.kind == "close":  # the units it has closed so far, for recovery
        entry["done"] = close.units_done()
    with deps.session_factory() as session:
        target = session.get(BotExit, close.exit_id)
        _log(target, deps, stage=stage, **entry)
        session.commit()


def _end(deps, close: _Close, state: str, event: str, fill=None, **extra) -> None:
    with deps.session_factory() as session:
        target = session.get(BotExit, close.exit_id)
        target.state = state
        if fill is not None:
            target.fill_price = fill
        _log(target, deps, event=event, **extra)
        session.commit()


def _closed(deps, close: _Close, fill=None, how: str = "") -> None:
    if close.kind == "close":
        return _close_filled(deps, close, fill, how)
    _end(deps, close, "filled", f"closed{how}", fill)
    _alert(
        deps,
        f"{close.bot.name}: {close.what} filled",
        f"{close.name} closed{how}.",
        close.bot.id,
        run_id=close.run.id,
        routine=True,  # filled: nothing is left to do
    )


def _unclear(deps, close: _Close, what: str) -> None:
    """An order may be live: nothing more is sent and the stop is not put back
    (a second closing order could over-close)."""
    _end(deps, close, "failed", "unclear: nothing more sent")
    _alert(
        deps,
        f"{close.bot.name}: {close.what} unclear",
        f"{what} and {close.name} may be without a stop. Check your broker.",
        close.bot.id,
        run_id=close.run.id,
    )


def _give_up(deps, close: _Close, why: str) -> None:
    """Nothing of the close is live: put the stop back for what's still open."""
    if close.kind == "close":
        return _close_ended(deps, close, "failed", f"gave up: {why}", why)
    _end(deps, close, "failed", f"gave up: {why}")

    def put_stop():
        with EXITS_LOCK:
            return rearm(deps, close.stop_id) if close.stop_id is not None else False

    if close.kind == "timed":
        put_back, again = _target_then_stop(deps, close, close.units - close.units_done(),
                                            put_stop)  # fmt: skip
        stop = (
            "the stop was put back for what's still open"
            if put_back
            else "the stop couldn't be put back"
        )
        target = f" and its profit target is {again}" if again else ""
        _alert(
            deps,
            f"{close.bot.name}: timed close didn't fill",
            f"The timed close for {close.name} didn't fully fill ({why}); {stop}{target}. The "
            "group is still open. Check your broker.",
            close.bot.id,
            run_id=close.run.id,
        )
        return
    put_back = put_stop()
    if put_back:
        detail = (
            f"The close for {close.name} didn't fully fill ({why}); "
            "the stop was put back for what's still open. Check your broker."
        )
    else:
        detail = (
            f"The close for {close.name} didn't fully fill ({why}) and the stop "
            "couldn't be put back. Check your broker."
        )
    _alert(
        deps,
        f"{close.bot.name}: profit target didn't fill",
        detail,
        close.bot.id,
        run_id=close.run.id,
    )


def request_cancel(session_factory, run_id: int, exit_id: int, now) -> bool:
    """Cancel on a running close (close_jobs.request_cancel): marks the run's target,
    only while it is "firing". The close's job sees it at its next step, stops,
    confirms its order is cancelled and puts the stop back. False if that close
    isn't running."""
    with session_factory() as session:
        row = session.get(BotExit, exit_id)
        if row is None or row.run_id != run_id or row.kind not in CLOSES:
            return False
        session.execute(
            update(BotExit)
            .where(
                BotExit.id == exit_id,
                BotExit.state == "firing",
                BotExit.cancel_requested_at.is_(None),
            )
            .values(cancel_requested_at=now)
        )
        session.commit()
        row = session.get(BotExit, exit_id)
        session.refresh(row)
        return row.state == "firing" and row.cancel_requested_at is not None


def cancel_reaches(session_factory, exit_id: int) -> bool:
    """Whether Cancel can still stop this close. Not once its leg-out has sent a sold
    leg at market, filled any of one, or started selling the buy legs: the leg-out
    then goes on to the end, or stops left to you (_stop_leg_out), never with a stop
    put back that can't close what's left."""
    with session_factory() as session:
        row = session.get(BotExit, exit_id)
        log = list(row.log) if row is not None else []
    return not any(
        e.get("stage") == "leg_out"
        and (
            "market" in e
            or "selling" in e
            or "bought_back" in e
            or e.get("status") in ("filled", "partial")
        )
        for e in log
    )


def _asked(deps, exit_id: int) -> bool:
    """Whether Cancel was pressed on this close (the job's _ensure_owned check)."""
    with deps.session_factory() as session:
        return session.get(BotExit, exit_id).cancel_requested_at is not None


def _cancelled(deps, close: _Close) -> None:
    """stop_and_rearm: the close's order is confirmed cancelled (the chase confirms
    before it says "stopped"); record what filled and put the stop back."""
    closed = f"closed {close.units_done()} of {close.units}"
    if close.kind == "close":
        return _close_ended(deps, close, "cancelled", f"cancelled by you: {closed}", None)
    _end(deps, close, "cancelled", f"cancelled by you: {closed}")

    def put_stop():
        with EXITS_LOCK:
            return rearm(deps, close.stop_id) if close.stop_id is not None else False

    put_back, again = _target_then_stop(deps, close, close.units - close.units_done(), put_stop)
    tail = (
        "the stop is back for what's still open."
        if put_back
        else f"the stop couldn't be put back. {close.name} may have no stop. Check your broker."
    )
    if again:
        tail += f" Its profit target is {again}."
    _alert(
        deps,
        f"{close.bot.name}: Close cancelled",
        f"The close for {close.name} was cancelled: {closed}; {tail}",
        close.bot.id,
        run_id=close.run.id,
    )


def _left_open(close: _Close) -> int | None:
    """What a close you asked for leaves the side holding, from its own fills: None
    if it ended before it read what was held (nothing of it was sent)."""
    start = close.progress.get("open_at_start")
    return None if start is None else start - close.units_done()


def _protect_rest(deps, stop_id: int | None, left: int | None) -> bool | None:
    """The side's stop placed again after a close you asked for: sized to `left`, what
    the close's own fills leave open, and by rearm to no more than the broker shows
    held, so it never covers more than is held. Not a re-arm (the close pulled it).
    None if there's no stop, or nothing is left; else whether it's working. Never
    while exits are off (rearm sends nothing then)."""
    if stop_id is None or (left is not None and left <= 0):
        return None
    with EXITS_LOCK:
        if left is not None:
            with deps.session_factory() as session:
                stop = session.get(BotExit, stop_id)
                stop.contracts = min(stop.contracts, left)
                session.commit()
        return rearm(deps, stop_id, first=True)


def _rest_words(deps, close: _Close, put_back, again: str, left) -> str:
    """What became of the side's protection after a close you asked for, in words."""
    if put_back is None:
        off = _side_off(deps, close)
        if off and (left is None or left > 0):
            if off == "both":
                return "exits are off for this side, so what's left is not protected"
            return "the stop is off for this side, so what's left has no stop"
        return "nothing is left to protect" if not left else "this side has no stop"
    size = f"the {left}" if left is not None else "what's"
    stop = (
        f"the stop is back for {size} still open"
        if put_back
        else "the stop couldn't be put back: that side may have no stop"
    )
    return stop + (f" and its profit target is {again}" if again else "")


def _side_off(deps, close: _Close) -> str | None:
    """ "both" (exits off), "stop" (only the stop switched off: nothing puts it back), or
    None."""
    with deps.session_factory() as session:
        row = session.get(BotExit, close.exit_id)
        if exits_off(session, row.run_id, row.group_position):
            return "both"
        return "stop" if stop_off(session, row.run_id, row.group_position) else None


def _then_of(deps, close: _Close) -> dict | None:
    with deps.session_factory() as session:
        row = session.get(BotExit, close.exit_id)
        return next((e["then_stop"] for e in row.log if "then_stop" in e), None)


def level_problem(
    calc, old: Decimal | None, ask: Decimal | None = None, pnl: Decimal | None = None
) -> str | None:
    """Why a profit action's new stop (stops.level_calc) must not be applied, or None
    (scale_out._stop_move_refusal, management._group_stop_live_check): it can't be
    worked out, it isn't tighter than the stop in force (a profit action never
    loosens it), or it's at or below the short's live ask (it would fire at once;
    with no fresh quote there's no telling). A watched group's P&L level
    (stops.watched_level, slice 13) is tighter when higher, and fires at once at or
    above the group's live P&L (`pnl`)."""
    if calc.problem is not None:
        return calc.problem
    if calc.basis.startswith("group_"):
        if old is not None and calc.trigger <= old:
            return f"the stop is already at {old}, as tight or tighter than {calc.trigger}"
        return fires_at_once(calc.trigger, pnl)
    if old is not None and calc.trigger >= old:
        return f"the stop is already at {old}, as tight or tighter than {calc.trigger}"
    if ask is None:
        return f"no fresh quote for the short to show a stop at {calc.trigger} won't fire at once"
    if calc.trigger <= ask:
        return (
            f"a stop at {calc.trigger} is at or below the short's ask {ask}, so it would "
            "fire at once"
        )
    return None


def profit_stop_calc(g: _Group, pct: Decimal | None, by: str, usd: Decimal | None = None):
    """A profit action's new stop for group `g` (scale_out: stop_level_for_pct), or None
    while a fill price is missing. A resting stop: the P&L level `pct` % of the entry
    lost, on its anchor, per contract of the anchor's ratio (stops.level_calc). A group
    the app watches: that P&L level per unit (stops.watched_level). With `usd` (bots slice
    19, adjust_sl_usd) the level is that $ P&L per contract instead (stops.level_usd_calc,
    watched_level_usd). The caller checks it with level_problem."""
    if g.entry is None:
        return None
    if g.anchor is None:
        return stops.profit_move(g.entry, pct, by, usd=usd)
    anchor_fill = _fill_of(g.fills, g.anchor.symbol)
    if anchor_fill is None:
        return None
    return stops.profit_move(g.entry, pct, by, anchor=g.anchor.symbol, anchor_fill=anchor_fill,
                             ratio=g.anchor.ratio, usd=usd)  # fmt: skip


def then_stop_of(action) -> dict | None:
    """A profit action's stop move as its close carries it (fire_close's then_stop):
    {"pct"} or, from slice 19, {"usd"}; None when it moves nothing."""
    if action.stop_usd is not None:
        return {"usd": f"{action.stop_usd:f}"}
    if action.stop_pct is not None:
        return {"pct": f"{action.stop_pct:f}"}
    return None


def _then_stop(deps, close: _Close) -> str | None:
    """A profit action's stop move, riding on its close (scale_out.execute_action,
    steps 5-6): once the close has filled, the stop row takes the new trigger before
    it's placed again for what's left, unless level_problem says no (then the old
    one goes back). None when the close carries no move. Never raises: a move that
    can't be worked out leaves the old stop.

    A resting stop: the P&L level on its anchor (stops.level_calc on the group's entry,
    per contract of the anchor's ratio), checked against the anchor's fresh ask. A group
    the app watches (slice 13): its P&L level at the % of the entry's size
    (stops.watched_level), checked against the group's live P&L; nothing is sent for it."""
    then = _then_of(deps, close)
    if then is None:
        return None
    by = then.get("by", "a profit action")
    revived = False
    if close.stop_id is None:  # the stop switched off: the action turns it back on
        with deps.session_factory() as session:
            row = session.get(BotExit, close.exit_id)
            run_id, position = row.run_id, row.group_position
        with EXITS_LOCK:
            close.stop_id = revive_stop(deps, run_id, position, by)
        if close.stop_id is None:
            return None
        revived = True
    try:
        with deps.session_factory() as session:
            run_row = session.get(BotRun, session.get(BotExit, close.exit_id).run_id)
            stop = session.get(BotExit, close.stop_id)
            old, watched_group = stop.trigger, bool(stop.watched)
            anchor = None if watched_group else _anchor_leg(stop)
            g = _Group(close.legs, anchor, 0, {f["symbol"]: f for f in run_row.filled_legs},
                       group_entry_of(run_row, close.legs), run_row.filled_at)  # fmt: skip
        usd = then.get("usd")
        calc = profit_stop_calc(g, None if usd else Decimal(then["pct"]), by,
                                usd=None if usd is None else Decimal(usd))  # fmt: skip
        if calc is None:
            problem = "the group has no fill prices"
        elif anchor is None:  # a revived stop has no stop in force to be tighter than
            problem = level_problem(calc, None if revived else old, pnl=_live_pnl(deps, g))
        else:
            problem = level_problem(calc, None if revived else old,
                                    _fresh_ask(deps, anchor.symbol))  # fmt: skip
        with deps.session_factory() as session:
            stop = session.get(BotExit, close.stop_id)
            if problem is None:
                stop.trigger = calc.trigger
                entry = calc.as_log()
                entry["stop_calc"]["moved_by"] = by
                _log(stop, deps, event=f"moved by {by}", **entry)
            else:
                _log(stop, deps, event=f"not moved: {problem}")
            session.commit()
            moved_group = (stop.run_id, stop.group_position) if problem is None else None
        if moved_group is not None:  # slice 21: the latest change wins
            clear_stop_style(deps, *moved_group)
    except Exception:  # noqa: BLE001 -- the old stop goes back
        logger.exception("couldn't work out the new stop after close %s", close.exit_id)
        problem = "something went wrong working it out"
        if not revived:
            return "the stop wasn't moved (something went wrong working it out)"
    if problem is not None and revived:  # it was off: it stays off, never at the old level
        unrevive_stop(deps, run_id, position, close.stop_id, problem)
        close.stop_id = None
        return f"the stop stays off: {problem}"
    if problem is not None:
        return f"the stop wasn't moved: {problem}"
    if revived:
        return f"{STOP_BACK_ON} at {calc.trigger} ({by})"
    return f"the stop moved {old} → {calc.trigger} ({by})"


def _close_filled(deps, close: _Close, fill, how: str) -> None:
    """A close you asked for filled. Everything still open closed: the side is closed.
    Part of it: "reduced", and the rest protected again for exactly what's left."""
    done, left = close.units_done(), _left_open(close)
    if left is None or left <= 0:
        _end(deps, close, "filled", f"closed{how}", fill, closed=done)
        _alert(
            deps, f"{close.bot.name}: close filled",
            f"{close.name} closed{how}.", close.bot.id, run_id=close.run.id,
            routine=True,
        )  # fmt: skip
        return
    start = close.progress["open_at_start"]
    _end(deps, close, "reduced", f"closed {done} of {start}{how}", fill, closed=done)
    moved = _then_stop(deps, close)  # a profit action's new stop, for what's left
    put_back, again = _target_then_stop(
        deps, close, left, lambda: _protect_rest(deps, close.stop_id, left)
    )
    moved = f" {moved[0].upper()}{moved[1:]}." if moved else ""
    _alert(
        deps,
        f"{close.bot.name}: closed {done} of {start}",
        f"Closed {done} of {start} on {close.name}{how}; "
        f"{_rest_words(deps, close, put_back, again, left)}.{moved}",
        close.bot.id,
        run_id=close.run.id,
    )


def _close_ended(deps, close: _Close, state: str, event: str, why: str | None) -> None:
    """A close you asked for that gave up or was cancelled: whatever it closed is
    recorded ("reduced"), and the side protected again for what it leaves open."""
    done, left = close.units_done(), _left_open(close)
    if left is not None and left <= 0 and done > 0:
        return _close_filled(deps, close, None, "")
    _end(deps, close, "reduced" if done > 0 else state, event, closed=done)
    put_back, again = _target_then_stop(
        deps, close, left, lambda: _protect_rest(deps, close.stop_id, left)
    )
    rest = _rest_words(deps, close, put_back, again, left)
    if _then_of(deps, close) is not None:  # scale_out: an unfilled close keeps the old stop
        rest += "; the stop wasn't moved, as the close didn't fill"
    if why is None:
        _alert(
            deps, f"{close.bot.name}: Close cancelled",
            f"The close for {close.name} was cancelled: closed {done} of "
            f"{close.units}; {rest}.", close.bot.id, run_id=close.run.id
        )  # fmt: skip
        return
    _alert(
        deps,
        f"{close.bot.name}: close didn't fill",
        f"The {close.noun} on {close.name} didn't fully fill ({why}): closed "
        f"{done} of {close.units}; {rest}. The group is still open. Check your broker.",
        close.bot.id,
        run_id=close.run.id,
    )


def _session_open(deps) -> bool:
    """The regular session (exit_chase._Chase.market_is_closed): never a natural
    stage, a leg-out or a market order after the close."""
    return session_now("SPY", deps.now().astimezone(MARKET_TZ)) == "regular"


def _natural(deps, close: _Close) -> Decimal | None:
    """The group's close at the current natural, from fresh quotes
    (exit_math.natural_price): None without a fresh, positive ask on every sell leg; a
    buy leg with no fresh quote sells for 0."""
    return exit_math.natural_price(close.legs, _fresh_quotes(deps, close), close.sign)


def _natural_then_leg_out(deps, close: _Close) -> None:
    """exit_chase EXHAUST_NATURAL: re-price the group's close to the current natural
    for the window, then leg out. A partly filled order is never replaced: the
    chase cancels it and a fresh order goes out for the units left, while every leg
    has filled the same units; once the legs are uneven, the rest is legged out."""
    plan = close.bot.exit_chase
    if not _session_open(deps):
        return _give_up(deps, close, "the session is over")
    tries = max(1, plan.natural_minutes * 60 // plan.interval_seconds)
    _stage(deps, close, "natural", tries=tries)
    while tries > 0:
        if _asked(deps, close.exit_id):
            return _cancelled(deps, close)
        units = close.units - close.units_done()
        if units <= 0 or not close.even():
            break  # nothing left, or the legs filled unevenly: leg out what's left
        if not _session_open(deps):
            return _give_up(deps, close, "the session ended")
        price = _natural(deps, close)
        if price is None:
            deps.sleep(plan.interval_seconds)  # no fresh quote: wait for one
            tries -= 1
            continue
        moves = {"n": 0}

        def follow(current, step, moves=moves):
            moves["n"] += 1
            fresh = _natural(deps, close)
            return current if fresh is None else fresh

        order = Order(_closing_legs(close.legs), units, False, price=price)
        out = chase(
            deps, _request(close.bot, close.run, order), price, replace(plan, max_tries=tries),
            follow, on_order=lambda oid: _note_order(deps, close.exit_id, oid),
            should_stop=lambda: _asked(deps, close.exit_id),
        )  # fmt: skip
        close.count(out.order)
        _stage(deps, close, "natural", status=out.status, chase=out.log)
        tries -= moves["n"] + 1
        if out.status == "filled":
            if close.units_done() >= close.units:
                return _closed(deps, close, price, " at the natural price")
            continue
        if out.status == "unclear":
            return _unclear(deps, close, f"The close for {close.name} may still be working")
        if out.status == "stopped":
            return _cancelled(deps, close)
        if out.status in ("refused", "preview"):
            return _give_up(deps, close, out.message or out.status)
    if close.units_done() >= close.units:
        return _closed(deps, close, None, " at the natural price")
    _leg_out(deps, close)


def _last_price(log, first: Decimal) -> Decimal:
    """The last price a chase put out (its log's last "price"), else its first."""
    sent = [e["price"] for e in log if isinstance(e, dict) and "price" in e]
    return Decimal(sent[-1]) if sent else first


def _keep_conceding(deps, close: _Close, last: Decimal) -> None:
    """exit_chase EXHAUST_KEEP (_Chase.keep_conceding): past the last try, keep
    conceding one step at a time from `last`, the chase's last price, with no try cap,
    but never past the current natural (re-read every step): past it the close would
    only pay for a worse fill. It runs until it fills, Cancel, or the session's close.
    A partly filled order is never replaced; once the legs are uneven, the rest is
    legged out, as the natural stage does."""
    plan = close.bot.exit_chase
    if not _session_open(deps):
        return _give_up(deps, close, "the session is over")
    # A session's worth of tries a round: the chase only ends early by filling,
    # partly filling, Cancel or the session's close.
    rounds = replace(plan, max_tries=max(1, 390 * 60 // plan.interval_seconds))
    _stage(deps, close, "keep")
    price = last
    while True:
        if _asked(deps, close.exit_id):
            return _cancelled(deps, close)
        units = close.units - close.units_done()
        if units <= 0 or not close.even():
            break  # nothing left, or the legs filled unevenly: leg out what's left
        if not _session_open(deps):
            return _give_up(deps, close, "the session ended")
        order = Order(_closing_legs(close.legs), units, False, price=price)
        conceding = _chase_step(order, close.legs)

        def concede(current, step, conceding=conceding):
            # One step on (a receiving close stays at its one-tick floor), then never
            # past the natural: higher is better for the close either way.
            found = conceding(current, step)
            found = current if found is None else found
            natural = _natural(deps, close)
            return found if natural is None else max(found, natural)

        price = concede(price, plan.step)
        order = replace(order, price=price)
        out = chase(
            deps, _request(close.bot, close.run, order), price, rounds, concede,
            on_order=lambda oid: _note_order(deps, close.exit_id, oid),
            should_stop=lambda: _asked(deps, close.exit_id) or not _session_open(deps),
        )  # fmt: skip
        close.count(out.order)
        _stage(deps, close, "keep", status=out.status, chase=out.log)
        price = _last_price(out.log, price)
        if out.status == "filled":
            if close.units_done() >= close.units:
                return _closed(deps, close, price, " by conceding on")
            continue
        if out.status == "unclear":
            return _unclear(deps, close, f"The close for {close.name} may still be working")
        if out.status == "stopped":
            if _asked(deps, close.exit_id):
                return _cancelled(deps, close)
            return _give_up(deps, close, "the session ended")
        if out.status in ("refused", "preview"):
            return _give_up(deps, close, out.message or out.status)
    if close.units_done() >= close.units:
        return _closed(deps, close, None, " by conceding on")
    _leg_out(deps, close)


def _positions(deps, run: _Run) -> dict[str, int]:
    """The run's account's option positions at its broker, by plain symbol (signed).
    May raise."""
    return {plain(k): v for k, v in _broker(deps, run).option_positions(run.account).items()}


def _leg_out(deps, close: _Close) -> None:
    """exit_chase._Chase.leg_out: every sell leg bought back first, one at a time, each
    its own chase ending at market; a buy leg is sold only once the close's own fills
    prove every sell leg bought back (SHORT_NOT_ATTRIBUTABLE), each on its own wing
    row. A multi-leg order never goes to market."""
    if _asked(deps, close.exit_id):
        return _cancelled(deps, close)
    if not _session_open(deps):
        return _give_up(deps, close, "the session ended")
    sells = [leg for leg in close.legs if leg.side is Side.SELL]
    buys = [leg for leg in close.legs if leg.side is Side.BUY]
    _stage(deps, close, "leg_out")
    try:
        held = _positions(deps, close.run)
    except Exception:  # noqa: BLE001 -- never size a leg-out on a guess
        logger.exception("couldn't read positions to leg out")
        return _give_up(deps, close, "couldn't read positions to leg out")

    def rest(leg) -> int:
        return close.units * leg.ratio - close.done(leg.symbol)

    if any(max(-held.get(plain(leg.symbol), 0), 0) < rest(leg) for leg in sells):
        # NOT_ATTRIBUTABLE: someone else bought some of a sold leg back.
        return _stop_leg_out(
            deps, close, "the position was changed outside this close, so it wasn't legged out"
        )
    for i, leg in enumerate(sells):
        size = rest(leg)
        if size > 0 and i and not _session_open(deps):
            return _stop_leg_out(deps, close, "the session ended")
        ended = _buy_back(deps, close, leg, size) if size > 0 else "closed"
        if ended == "closed" and rest(leg) <= 0:  # proven by this close's own fills
            _stage(deps, close, "leg_out", bought_back=leg.symbol)
        if ended == "closed":
            continue
        unsent = _unsent_words(sells[i + 1 :], buys)
        if ended == "unclear":
            return _unclear(
                deps, close, f"Buying back {leg.symbol.strip()} may still be working{unsent}"
            )
        if ended == "unrecorded":
            return _unclear(
                deps,
                close,
                f"A market order buying back {leg.symbol.strip()} may still be working (the "
                f"app couldn't record it){unsent}",
            )
        if ended == "stopped":
            return _cancelled(deps, close)
        return _stop_leg_out(deps, close, ended)
    unproven = [leg for leg in sells if rest(leg) > 0]
    if unproven:  # SHORT_NOT_ATTRIBUTABLE: never sell a buy leg
        names = _names(unproven)
        return _unclear(deps, close, f"{names} {_is(unproven)} not proven bought back")
    _sell_buys(deps, close, sells, buys, held, rest)


def _stop_leg_out(deps, close: _Close, why: str) -> None:
    """A leg-out that can't go on. Before any leg filled unevenly, as any give-up: the
    stop (and a timed close's target) goes back. Once this close has bought back any
    sell leg, or its legs filled unevenly, that stop can't close the group (its close
    is refused, "some legs gone"): the group is left to you, nothing put back."""
    if close.even() and not close.handed:
        return _give_up(deps, close, why)
    _left_to_you(deps, close, why)


def _left_to_you(deps, close: _Close, why: str) -> None:
    """A part-done leg-out: the close ends failed, no stop or target is put back, and
    the alert names every leg still held."""
    open_legs = [
        leg
        for leg in close.legs
        if close.done(leg.symbol) + close.handed.get(plain(leg.symbol), 0) < close.units * leg.ratio
    ]
    _end(deps, close, "failed", f"left to you: {why}", closed=close.units_done())
    held = (
        f"{_names(open_legs)} {_is(open_legs)} still held and nothing protects "
        f"{'it' if len(open_legs) == 1 else 'them'}"
        if open_legs
        else "nothing is protected"
    )
    _alert(
        deps,
        f"{close.bot.name}: {close.what} stopped part-way",
        f"The {close.what} for {close.name} stopped part-way through legging out ({why}). "
        f"{held}: no stop or target was put back. Check your broker.",
        close.bot.id,
        run_id=close.run.id,
    )


def _sell_buys(deps, close: _Close, sells, buys, held, rest) -> None:
    """The leg-out's buy legs, every sell leg proven bought back: each sold on its own
    wing row (_sell_out), sized to what's held. A leg handed to its row counts as
    closed for this close (_Close.handed); one not held, or not sold because the
    session is over, never does."""
    if buys:  # from here Cancel doesn't reach: the buy legs are sold either way
        _stage(deps, close, "leg_out", selling=[leg.symbol for leg in buys if rest(leg) > 0])
    sold, unheld, unsold, late = [], [], [], []
    for leg in buys:
        if rest(leg) <= 0:
            continue  # the combo's own fills sold it all
        size = min(rest(leg), max(held.get(plain(leg.symbol), 0), 0))
        if size <= 0:
            unheld.append(leg)
            continue
        if not _session_open(deps):  # never a buy leg's sale past the close
            late.append(leg)
            continue
        close.handed[plain(leg.symbol)] = size  # on its own row from here
        ok = _sell_out(deps, close, leg, size)
        (sold if ok else unsold).append(leg)
    _closed(deps, close, None, _legged_out_words(sells, sold, unheld, unsold, late))


def _sell_out(deps, close: _Close, leg, size: int) -> bool:
    """One buy leg of a leg-out, sold on a wing row of its own (the wing chase, never at
    market past the session's close). True if it sold or is working."""
    log = [{"at": deps.now().isoformat(), "event": "legging out: every sold leg is bought back"}]
    with deps.session_factory() as session:
        row = session.get(BotExit, close.exit_id)
        position, anchor = row.group_position, row.anchor_symbol
    wing_id = _new_wing_row(deps, close.run.id, position, anchor, leg, size, log)
    _stage(deps, close, "leg_out", handed=leg.symbol, wing=wing_id)  # its row's to sell now
    try:  # the row went in already in FIRING
        wings = _run_wing_chase(
            deps, wing_id, close.bot, close.run, leg.symbol, size, log, in_session=True
        )
    finally:
        FIRING.discard(wing_id)
    return wings[-1].state in ("working", "filled", "expired")


def _names(legs) -> str:
    return _listed([leg.symbol for leg in legs])


def _listed(symbols) -> str:
    """Symbols in words: "A", "A and B", "A, B and C"."""
    names = [symbol.strip() for symbol in symbols]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _is(legs) -> str:
    return "is" if len(legs) == 1 else "are"


def _was(legs) -> str:
    return "wasn't" if len(legs) == 1 else "weren't"


def _wings(legs, sells) -> str:
    """A leg-out's buy legs in words: "its wing X" ("its wings X and Y"), or for a group
    with no sell leg "X" ("X and Y")."""
    if not sells:
        return _names(legs)
    return f"its wing{'s' if len(legs) > 1 else ''} {_names(legs)}"


def _unsent_words(sells, buys) -> str:
    """What a leg-out stopped part-way didn't send, in words (empty if nothing)."""
    parts = []
    if sells:
        parts.append(f"{_names(sells)} {_was(sells)} bought back")
    if buys:
        parts.append(f"{_wings(buys, True)} {'was' if len(buys) == 1 else 'were'} not sold")
    return "; " + " and ".join(parts) if parts else ""


def _legged_out_words(sells, sold, unheld, unsold, late) -> str:
    """How a leg-out ended, for the close's alert."""
    if not sells:
        parts = ["legged out"]
    elif len(sells) == 1:
        parts = ["legged out: the short was bought back"]
    else:
        parts = ["legged out: every sold leg was bought back, one at a time"]
    if sold:
        parts.append(f"then {_wings(sold, sells)} sold")
    if unheld:
        parts.append(f"{_wings(unheld, sells)} {_was(unheld)} held")
    if late:
        parts.append(f"but the session is over, so {_wings(late, sells)} {_was(late)} sold")
    if unsold:
        parts.append(f"but {_wings(unsold, sells)} {_was(unsold)} sold")
    text = ", ".join(parts)
    return f" ({text}. Check your broker)" if late or unsold else f" ({text})"


def _buy_back(deps, close: _Close, leg, size: int) -> str:
    """One sell leg of a leg-out bought back (the plan's _buy_back): chased from its mid
    plus the start offset, one step (at least its tick) more each interval, then at
    market for what's left (switch_to_market). Returns "closed", "unclear",
    "unrecorded", "stopped", or why nothing is live (the stop goes back)."""
    short = leg.symbol
    plan = close.bot.exit_chase
    quote = _quote(deps, short)
    if quote is None or quote.ask is None or quote.ask <= 0:
        return f"no fresh quote for {short.strip()} to leg out"
    mid = _mid(deps, short)
    start = mid + plan.start_offset if mid is not None else quote.ask
    first = exit_math.buy_limit(short, start)
    probe = Order((Leg(short, Side.BUY),), 1, False, OrderType.MARKET)

    def higher(price, step):
        step = max(step, ticks.order_tick(probe, price, {}))  # execution._concede
        following = exit_math.buy_limit(short, -price + step)
        return following if following < price else None

    note = lambda oid: _note_order(deps, close.exit_id, oid)  # noqa: E731
    order = Order((Leg(short, Side.BUY),), size, False, price=first)
    out = chase(
        deps, _request(close.bot, close.run, order), first, plan, higher, on_order=note,
        should_stop=lambda: _asked(deps, close.exit_id) and cancel_reaches(
            deps.session_factory, close.exit_id),  # not once a sold leg is bought back
    )  # fmt: skip
    close.count(out.order)
    _stage(deps, close, "leg_out", leg=short, status=out.status, chase=out.log)
    if out.status == "filled":
        return "closed"
    if out.status in ("unclear", "stopped"):
        return out.status
    if out.status not in ("missed", "partial"):
        return out.message or out.status
    rest = size - (out.order.legs[0].quantity - out.order.legs[0].remaining if out.order else 0)
    if rest <= 0:
        return "closed"
    if not _session_open(deps):
        return "the session ended"
    market = Order((Leg(short, Side.BUY),), rest, False, OrderType.MARKET)
    result = orders.submit(
        deps.session_factory, deps.clients(), _request(close.bot, close.run, market),
        now=deps.now(), notify=deps.notify,
    )  # fmt: skip
    if _placed(result):
        close.progress["live_order"] = result.broker_order_id  # known before anything can break
    _stage(deps, close, "leg_out", leg=short, market=rest, result=result.message)
    if not _placed(result):
        return "unclear" if result.message.startswith(orders.UNCLEAR) else result.message
    if record_order(note, result.broker_order_id):
        return "unrecorded"  # it may be live and recovery couldn't find it: never re-arm
    close.progress.pop("live_order", None)  # on the target now, for recovery to settle
    for _ in range(3):
        deps.sleep(plan.interval_seconds)
        try:
            found = _broker(deps, close.run).get_order(close.run.account, result.broker_order_id)
        except Exception:  # noqa: BLE001 -- it may be live
            logger.exception("couldn't read the short's market order")
            return "unclear"
        if found.status is OrderStatus.FILLED or not found.may_be_working:
            close.count(found)
            return "closed" if found.status is OrderStatus.FILLED else f"market {found.status}"
    return "unclear"  # still working: watch it at the broker, sell nothing more
