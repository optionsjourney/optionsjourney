"""Managing one bot trade from the journal (spec: bots slice 6; Tracker2's
management.py, manual_close.py and override_tags.py).

Per side of a filled run: change its stop or target, turn its exits off (and back
on), close part of it. Each action is refused before anything is touched when it
can't be done, and a refusal saves nothing. The order machinery is bot_exits':

- a stop change is slice 4's stop move (the live-ask check, cancel → confirm →
  place, its marker and recovery)
- a close is slice 3's claim and close job, as a timed close
- exits off is checked by every path that sends or fires a group's exits
  (bot_exits.exits_off)

What you changed is kept per run and side in bot_trade_overrides, for the chips
(bots/manage.py) and the exits-off guard."""

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import exit_math, manage, stops, timing, watched_stops
from optionsjourney.db.models import BotExit, BotRun, BotTradeOverride
from optionsjourney.services import bot_exits, orders
from optionsjourney.services.bot_chase import read_after_cancel
from optionsjourney.services.bot_exits import EXITS_LOCK, EXITS_OFF, TURNING_OFF
from optionsjourney.trading.orders import OrderStatus, Side, plain

logger = logging.getLogger(__name__)
HOLDING = ("filled", "partial")
CHECK_BROKER = "Something went wrong part-way; check your broker."


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str


@dataclass(frozen=True)
class _Level:
    """A level you typed, in the shape bot_exits' moves read (a time action's)."""

    stop_pct: Decimal | None = None
    target_pct: Decimal | None = None


def _refused(message: str) -> Outcome:
    return Outcome(False, message)


# Bots slice 17 (spec question 6): a futures run's profit target rests at the broker and it
# has no stop; changing it from the app (Tracker2's drawer) isn't built yet.
FUTURES_NOT_YET = (
    "Futures: profit target only, no stop. Adding a stop, closing part or switching exits off "
    "on a futures trade from the app isn't built yet: do it at TastyTrade. You can change its "
    "target or switch it off here. The app tells you at once if the target goes."
)


def _futures_run(deps, run_id: int) -> bool:
    with deps.session_factory() as session:
        return bot_exits.pt_only(session, run_id)


def _rests_target(deps, run_id: int, position: int) -> bool:
    """The group's profit target rests at TastyTrade (a futures run's, slice 17, or one
    chosen to, decision 0048): changed, switched off and on there, never armed."""
    with deps.session_factory() as session:
        return bot_exits.pt_only(session, run_id) or bot_exits.target_rested(
            session, run_id, position
        )


def _target_range(deps, run_id: int) -> tuple[Decimal, Decimal]:
    """The % a target you type may be (decision 0048): by the trade's fill, not its bot."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        entry = None if run is None else (
            run.fill_credit if run.fill_credit is not None else run.target_credit
        )  # fmt: skip
    return manage.target_range(entry)


@dataclass
class _Side:
    """One group of a run, read under the lock (bots slice 13: any group): its legs per
    unit, its anchor (the leg a resting stop is on; None when the app watches the
    group's P&L), its kind, fills, exit rows and open units."""

    run: BotRun
    rows: list
    legs: tuple[exit_math.GroupLeg, ...]
    anchor: exit_math.GroupLeg | None
    kind: str  # "single_short" (a stop rests on the anchor) or "watched"
    fills: dict
    entry_credit: Decimal | None  # the run's whole credit, for a resting stop's bases
    size: int  # open units
    stop_off: bool  # bots slice 18: the stop switched off (Tracker2's stop_loss_off)
    target_off: bool  # profit_target_off
    group: bot_exits._Group

    @property
    def off(self) -> bool:
        """Both switched off: slice 6's "exits off"."""
        return self.stop_off and self.target_off

    @property
    def watched(self) -> bool:
        return self.kind == "watched"

    @property
    def state(self) -> str:
        return timing.side_state((r.kind, r.state) for r in self.rows)

    def latest_close(self):
        """The side's latest close of any kind (target, timed or yours)."""
        found = [r for r in self.rows if r.kind in bot_exits.CLOSES]
        return max(found, key=lambda r: r.id) if found else None

    def latest(self, kind: str, *states: str):
        found = [r for r in self.rows if r.kind == kind and (not states or r.state in states)]
        return max(found, key=lambda r: r.id) if found else None

    def sides(self) -> bot_exits._Group:
        """The group, for bot_exits' moves."""
        return self.group


def _read(session, run_id: int, position: int) -> _Side | None:
    run = session.get(BotRun, run_id)
    if run is None or run.status not in HOLDING or not run.filled_legs:
        return None
    if not any(leg["group"] == position for leg in run.legs):
        return None
    rows = list(
        session.scalars(
            select(BotExit).where(BotExit.run_id == run_id, BotExit.group_position == position)
        )
    )
    credit = run.fill_credit if run.fill_credit is not None else run.target_credit
    units = bot_exits.group_units(session, run, position)
    watched = bot_exits.group_columns(session, run, position, rows)["watched"]
    g = bot_exits._group_of(run, rows, position, units, watched=watched)
    return _Side(
        run, rows, g.legs, g.anchor, "watched" if g.anchor is None else "single_short",
        g.fills, credit, units, bot_exits.stop_off(session, run_id, position),
        bot_exits.target_off(session, run_id, position), g,
    )  # fmt: skip


def _live_check(deps, side: _Side):
    """(ask, pnl) for checking a new stop won't fire at once: the anchor's fresh ask
    for a resting stop, or the group's live P&L for one the app watches
    (management._group_stop_live_check). Both None when there's no price."""
    if side.anchor is None:
        return None, bot_exits._live_pnl(deps, side.group)
    return bot_exits._fresh_ask(deps, side.anchor.symbol), None


def _no_price(side: _Side) -> str:
    return "the group's legs" if side.anchor is None else "the short"


def _not_open(side: _Side | None) -> str | None:
    """Why nothing can be done to this group now, or None."""
    if side is None:
        return "This trade isn't open."
    if side.state == "closed":
        return "This group is already closed."
    if side.state == "busy":
        return (
            "A close is running on this group, or its exits are still being placed. "
            "Try again when it's done."
        )
    return None


MAYBE_LIVE = (
    "A close, stop move or profit target on this group may still be working at your "
    "broker: check your broker first. Nothing was sent."
)


def _maybe_live(side: _Side) -> bool:
    """Whether an order the app can't vouch for may still be working on this group, so
    nothing new may be sent for it (bot_exits.held_back)."""
    return bot_exits.held_back(side.rows) is not None


def _replaceable(stop) -> bool:
    """A stop that isn't working may be replaced by a new one only when it's known to
    be dead: it failed, the broker showed it cancelled or rejected, or the close that
    pulled it is over at the broker."""
    if stop.state == "failed":
        return True
    last = stop.log[-1].get("event", "") if stop.log else ""
    # Pulled by a close: only reached once _maybe_live says that close is over at the
    # broker (settle_side released the side).
    return stop.state == "cancelled" and (
        str(last).startswith("broker shows") or last in bot_exits.CLOSE_MARKERS
    )


def _upsert(session, deps, run_id: int, position: int, what: str, result: str, **fields):
    """The run's override for this group, updated (Tracker2: look up, then update or
    insert, never a blind insert), with the change logged. The caller commits."""
    now = deps.now()
    row = session.scalar(
        select(BotTradeOverride).where(
            BotTradeOverride.run_id == run_id, BotTradeOverride.group_position == position
        )
    )
    if row is None:
        row = BotTradeOverride(run_id=run_id, group_position=position, log=[])
        session.add(row)
    for name, value in fields.items():
        setattr(row, name, value)
    row.changed_at = now
    row.log = [*(row.log or []), {"at": now.isoformat(), "what": what, "result": result}]
    return row


def _save(deps, run_id: int, position: int, what: str, result: str, **fields) -> None:
    """Record what took effect on this group."""
    with deps.session_factory() as session:
        _upsert(session, deps, run_id, position, what, result, **fields)
        session.commit()


def _group(bot, position: int):
    return next((g for g in bot.groups if g.position == position), None)


def _sentence(note: str) -> str:
    return note[:1].upper() + note[1:] + ("" if note.endswith(".") else ".")


STOP_UNITS = ("pct", "usd")  # bots slice 21: Tracker2's per-trade stop_loss_unit


def _stop_typed(text: str, unit: str | None):
    """(pct, usd, what, problem) for a stop you typed: a % (the group's own style) or $ a
    contract (bots slice 21)."""
    unit = (unit or "pct").strip().lower()
    if unit not in STOP_UNITS:
        return None, None, "", "The stop is a % or $ a contract."
    if unit == "usd":
        usd, problem = manage.read_usd(text, "stop")
        return None, usd, "" if problem else f"stop {manage._money(usd)} a contract", problem
    pct, problem = manage.read_pct(text, manage.STOP_RANGE, "stop")
    return pct, None, "" if problem else f"stop {manage._num(pct)}%", problem


def change_stop(deps, run_id: int, position: int, text: str, unit: str = "pct") -> Outcome:
    """Move one side's stop to the % you typed (management.apply_override with a
    stop %): checked against the short's fresh ask before anything is cancelled
    (_group_stop_live_check), then slice 4's move. A side whose stop failed or was
    cancelled gets a new one at that level. On a group the app watches (bots slice
    13) your % is "% of the entry price lost": its P&L level moves, checked against
    the group's live P&L, and nothing is cancelled or sent. Bots slice 21: or `unit`
    "usd", $ a contract (Tracker2's per-trade stop_loss_unit/usd): the short's fill plus
    the dollars, or a watched group's level at -$. Either clears this trade's own
    closing order: the latest change wins."""
    if _futures_run(deps, run_id):
        return _refused(FUTURES_NOT_YET)
    pct, usd, what, problem = _stop_typed(text, unit)
    if problem:
        return _refused(problem)
    fields = {"stop_pct": pct, "stop_unit": "usd" if usd is not None else None,
              "stop_usd": usd, "co_mode": None, "co_amount": None}  # fmt: skip
    return _set_stop(
        deps, run_id, position, what, fields,
        lambda group, side: bot_exits._moved_calc(
            group, pct, side.sides(), side.entry_credit, "you", usd=usd
        ),
    )  # fmt: skip


def set_closing_order(deps, run_id: int, position: int, mode: str,
                      amount_text: str = "") -> Outcome:  # fmt: skip
    """This trade's own closing-order overlay on one group (bots slice 21; Tracker2's
    per-group stop_loss_co_mode / stop_loss_co_amount override, "deliberately per-group,
    not run-wide"): the group's own stop (its % style at this trade's %) overlaid as
    stops.stop_calc does (both: the dollars off the entry credit replace it; tighter: they
    win only when lower), then the same live check and move as Change stop. Off puts back
    the group's own number. Only a credit group whose stop rests on its short has one;
    a $ stop has no overlay."""
    from optionsjourney.bots.definition import CO_MODES

    if _futures_run(deps, run_id):
        return _refused(FUTURES_NOT_YET)
    mode = (mode or "").strip().lower()
    if mode not in CO_MODES:
        return _refused("Pick Off, Every group or When tighter for the closing order.")
    amount = None
    if mode != "off":
        amount, problem = manage.read_usd(amount_text, "closing order")
        if problem:
            return _refused(f"{problem} The closing order is the dollars off the entry credit.")
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        entry = None if run is None else (
            run.fill_credit if run.fill_credit is not None else run.target_credit
        )  # fmt: skip
    if entry is not None and entry < 0:
        return _refused(
            "This trade opened for a debit: the closing order works only on a credit trade's "
            "stop on its short."
        )
    what = "closing order off" if mode == "off" else (
        f"closing order {manage._money(amount)} ({mode})"
    )  # fmt: skip

    def work(group, side):
        if side.anchor is None:
            return None, "a group the app watches has no closing order"
        if group is None or group.stop_value is None:
            return None, "the group has no stop to overlay"
        if group.stop_unit == "usd":
            return None, "a $ stop has no closing order; set a % stop first"
        fill = bot_exits._fill_of(side.fills, side.anchor.symbol)
        if fill is None:
            return None, "the short has no fill price"
        calc = stops.stop_calc(group, mode, amount, side.anchor.symbol, fill, side.entry_credit,
                               ratio=side.anchor.ratio)  # fmt: skip
        if calc.problem is not None:
            return None, calc.problem
        entry = calc.as_log()
        entry["stop_calc"]["moved_by"] = "you"
        return (calc, entry), None

    return _set_stop(deps, run_id, position, what, {"co_mode": mode, "co_amount": amount}, work)


def _set_stop(deps, run_id: int, position: int, what: str, fields: dict, work) -> Outcome:
    """The stop moved to what `work(group, side)` works out ((calc, log), problem), with
    `group` the bot's group as this trade runs it (overrides.effective_group): the live
    check first (the short's fresh ask, or a watched group's P&L), then slice 4's move
    (cancel, confirm, place) on a working stop, or a new stop on one known dead. `fields`
    are saved once the trigger changed."""
    with EXITS_LOCK:
        _, bot = bot_exits._run_and_bot(deps, run_id)
        with deps.session_factory() as session:
            side = _read(session, run_id, position)
            why = _not_open(side)
            if why:
                return _refused(why)
            if side.off:
                return _refused(
                    "Exits are off for this group. Turn them back on; you can set the stop there."
                )
            if side.stop_off:
                return _refused(
                    "The stop is off for this group. Switch it back on; you can set the stop there."
                )
            live = side.latest("stop", *bot_exits.STOP_LIVE)
            latest = side.latest("stop")
            if latest is None:
                return _refused("This group has no stop to change.")
            if _maybe_live(side):
                return _refused(MAYBE_LIVE)
            if live is None and not _replaceable(latest):
                return _refused(MAYBE_LIVE)
            target_id = live.id if live is not None else latest.id
            before = session.get(BotExit, target_id).trigger
            group = bot_exits.effective_group(session, run_id, bot, position)
        ask, pnl = _live_check(deps, side)
        if ask is None and pnl is None:
            return _refused(
                f"There's no fresh price for {_no_price(side)}, so the new stop can't be "
                "checked. Nothing was changed; try again in a moment."
            )
        found, problem = work(group, side)
        if problem is not None:
            verb = "moved" if live is not None else "placed"
            return _refused(_sentence(f"stop not {verb}: {problem}"))
        if live is None:  # a new stop: never sized for more than is left open
            with deps.session_factory() as session:
                row = session.get(BotExit, latest.id)
                row.contracts = min(row.contracts, side.size)
                session.commit()
        run, _ = bot_exits._run_and_bot(deps, run_id)
        sides = side.sides()
        progress: dict = {}
        try:
            if live is not None:
                moved, note = bot_exits._move_stop(
                    deps, run, bot, group, live.id, sides, side.entry_credit, None, "you",
                    progress, ask, marker=bot_exits.MOVING_STOP_BY_YOU, found=found, pnl=pnl,
                )  # fmt: skip
            else:
                moved, note = bot_exits.place_stop_at(
                    deps, latest.id, group, sides, side.entry_credit, None, ask, "you", pnl=pnl,
                    found=found,
                )  # fmt: skip
        except Exception as e:  # noqa: BLE001 -- say what's left; never raise to the page
            logger.exception("changing the stop of run %s side %s failed", run_id, position)
            moved, note = False, bot_exits._broke_note(deps, target_id, "stop", e, progress)
            bot_exits._alert(
                deps, f"{bot.name}: stop change broke", _sentence(note), bot.id, run_id=run_id
            )
        with deps.session_factory() as session:
            after = session.get(BotExit, target_id).trigger
        if after != before:  # the stop now carries your level, placed or waiting
            _save(deps, run_id, position, what, note, **fields)
    return Outcome(moved, _sentence(note))


def change_target(deps, run_id: int, position: int, text: str) -> Outcome:
    """Re-price one side's armed target to the % of its credit you typed, or arm one
    (apply_override with a target %). Bots slice 21: a futures trade's resting target is
    cancelled and placed again at TastyTrade (_resting_change_target); decision 0048: so is
    any group's that rests. The % may be 1–1000 on a trade that filled for a debit, 1–99 on
    a credit."""
    if _rests_target(deps, run_id, position):
        return _resting_change_target(deps, run_id, position, text)
    pct, problem = manage.read_pct(text, _target_range(deps, run_id), "target")
    if problem:
        return _refused(problem)
    with EXITS_LOCK:
        with deps.session_factory() as session:
            side = _read(session, run_id, position)
            why = _not_open(side)
            if why:
                return _refused(why)
            if side.off:
                return _refused("Exits are off for this group. Turn them back on first.")
            if side.target_off:
                return _refused("The target is off for this group. Switch it back on first.")
            armed = side.latest("target", "armed")
        moved, note = bot_exits._move_target(
            deps, run_id, position, armed.id if armed else None, side.sides(), _Level(None, pct),
            "you",
        )  # fmt: skip
        if moved:
            _save(deps, run_id, position, f"target {manage._num(pct)}%", note, target_pct=pct)
    return Outcome(moved, _sentence(note))


def preview_stop(deps, run_id: int, position: int, text: str, unit: str = "pct") -> Outcome:
    """What Change stop would set for the % you typed, without saving or sending
    anything (bots slice 18; Tracker2's automation_bp.api_run_preview_stop over
    exits._preview_stop: "Writes nothing to the database and calls no broker
    endpoint"). The same arithmetic change_stop uses (bot_exits._moved_calc), then the
    short's fresh ask from the quote stream (or a watched group's live P&L) to say
    whether it would fire at once now."""
    if _futures_run(deps, run_id):
        return _refused(FUTURES_NOT_YET)
    pct, usd, what, problem = _stop_typed(text, unit)
    if problem:
        return _refused(problem)
    _, bot = bot_exits._run_and_bot(deps, run_id)
    with deps.session_factory() as session:
        side = _read(session, run_id, position)
        why = _not_open(side)
        if why:
            return _refused(why)
        if side.stop_off:
            return _refused("The stop is off for this group; switch it back on to set one.")
        latest = side.latest("stop")
        if latest is None:
            return _refused("This group has no stop to change.")
        live = side.latest("stop", *bot_exits.STOP_LIVE)
        now = live.trigger if live is not None else None
        group = bot_exits.effective_group(session, run_id, bot, position)
    found, problem = bot_exits._moved_calc(
        group, pct, side.sides(), side.entry_credit, "you", usd=usd
    )
    typed = what.removeprefix("stop ")
    if problem:
        return _refused(f"A {typed} stop can't be set: {problem}. Nothing changed.")
    calc, entry = found
    how = " · ".join(stops.describe(stops.calc_of([entry])))
    level = f"P&L {watched_stops.signed(calc.trigger)}" if side.watched else f"{calc.trigger}"
    was = "" if now is None else (
        f" (now P&L {watched_stops.signed(now)})" if side.watched else f" (now {now})"
    )  # fmt: skip
    ask, pnl = _live_check(deps, side)
    if side.watched:
        fires = bot_exits.fires_at_once(calc.trigger, pnl)
        market = _sentence(fires) if fires else (
            f"The group's P&L is {watched_stops.signed(pnl)}: it wouldn't fire at once now."
        )  # fmt: skip
    elif ask is None:
        market = "There's no fresh price for the short, so Change stop would refuse it now."
    elif calc.trigger <= ask:
        market = f"The short's ask is {ask}: it would fire at once, so Change stop would refuse it."
    else:
        market = f"The short's ask is {ask}: it wouldn't fire at once now."
    return Outcome(
        True,
        f"Preview, nothing changed: a {typed} stop would be at {level}{was}"
        f"{': ' + how if how else ''}. {market}",
    )


# --- Bots slice 21: a group's own settings on this trade --------------------------------


def _wing_side(deps, run_id: int, position: int):
    """(the side, the bot's group, the legs its stop leaves) or (None, None, why not):
    a held, open group whose stop rests on its short and leaves legs behind."""
    if _futures_run(deps, run_id):
        return None, None, FUTURES_NOT_YET
    with deps.session_factory() as session:
        side = _read(session, run_id, position)
    why = _not_open(side)
    if why:
        return None, None, why
    _, bot = bot_exits._run_and_bot(deps, run_id)
    group = _group(bot, position)
    if group is None:
        return None, None, "This group isn't in the bot any more."
    if side.anchor is None:
        return (
            None,
            None,
            (
                "This group's stop is watched by the app and closes the whole group, so nothing "
                "is left after it."
            ),
        )
    left = len(side.legs) - 1
    if left < 1:
        return None, None, "This group is one short: nothing is left after its stop."
    return side, group, left


def set_wing_rule(deps, run_id: int, position: int, mode: str, limit_text: str = "") -> Outcome:
    """What this trade does with the group's other legs when its stop fills (Tracker2's
    per-run on_stop_remainder / on_stop_remainder_limit, management._apply_override_locked:
    "the group's own on_stop_remainder is left untouched, so future runs are unaffected").
    Validated as the bot's (validation._remainder_problem); refused once the group's stop
    has filled (its rest is already handled). Nothing is sent: it's read when the stop
    fills (bot_exits._handle_stop_fill) and by a paired close of this group."""
    from dataclasses import replace

    from optionsjourney.bots import validation

    mode = (mode or "").strip()
    limit = None
    text = (limit_text or "").strip()
    if text:
        try:
            limit = Decimal(text)
        except ArithmeticError, ValueError:
            return _refused("The limit price must be a number, or blank for the bid.")
        if not limit.is_finite():
            return _refused("The limit price must be a number, or blank for the bid.")
    if mode != "close_limit":
        limit = None  # only a limit sale has a price
    with EXITS_LOCK:  # never while the monitor is handling this group's stop fill
        side, group, left = _wing_side(deps, run_id, position)
        if side is None:
            return _refused(left)
        problem = validation._remainder_problem(
            replace(group, on_stop_remainder=mode, remainder_limit=limit), left
        )
        if problem:
            return _refused(problem)
        words = manage.wing_words(mode, limit)
        _save(deps, run_id, position, f"after the stop: {words}", "saved",
              on_stop_remainder=mode, remainder_limit=limit)  # fmt: skip
    paused = mode == "leave" and bot_store_leave_paused(deps)
    tail = ' While "left open" is paused, the wing is sold instead.' if paused else ""
    return Outcome(True, f"When this group's stop fills, this trade will {words}.{tail}")


def bot_store_leave_paused(deps) -> bool:
    from optionsjourney.services import bot_store

    return bot_store.leave_paused(deps.session_factory, deps.now())


def revert_wing_rule(deps, run_id: int, position: int) -> Outcome:
    """Back to the group's own wing rule for this trade."""
    with EXITS_LOCK:
        side, group, left = _wing_side(deps, run_id, position)
        if side is None:
            return _refused(left)
        _save(deps, run_id, position, "after the stop: the bot's", "reverted",
              on_stop_remainder=None, remainder_limit=None)  # fmt: skip
    words = manage.wing_words(group.on_stop_remainder, group.remainder_limit)
    return Outcome(True, f"This trade uses the group's own rule again: {words}.")


def _profit_side(deps, run_id: int, position: int):
    """(side, bot, the group as this trade runs it, done sigs) or (None, None, None, why
    not): a held group that's open and not closing."""
    from optionsjourney.services import bot_watch

    if _futures_run(deps, run_id):
        return None, None, None, FUTURES_NOT_YET
    _, bot = bot_exits._run_and_bot(deps, run_id)
    with deps.session_factory() as session:
        side = _read(session, run_id, position)
        why = _not_open(side)
        if why:
            return None, None, None, why
        groups = bot_watch.trade_groups(session, run_id, bot) if bot is not None else {}
    group = groups.get(position)
    if group is None:
        return None, None, None, "This group isn't in the bot any more."
    done, _ = bot_watch.profit_done(groups, side.rows)
    return side, bot, group, {sig for p, sig in done if p == position}


def _clear_profit_streaks(run_id: int, position: int) -> None:
    from optionsjourney.services import bot_watch

    prefix = f"{bot_watch.PROFIT}{position}:"
    bot_watch._forget(run_id, keep=lambda rule: not rule.startswith(prefix))


def set_profit_actions(deps, run_id: int, position: int, form) -> Outcome:
    """This trade's own profit actions on one group (bots slice 21; Tracker2's per-group
    BotPositionOverride.use_profit_actions / profit_actions, which "REPLACES the bot's
    list, never merges"): read from the form as the bot form reads a group's, validated
    as the bot's (validation._profit_actions) against this trade's target, and refused
    when an action that already ran on the group isn't in it as it was
    (management._fired_group_actions_unchanged). Nothing is sent; the watch pass reads it
    from its next pass, the group's streaks starting again (clear_group_confirmation)."""
    from dataclasses import replace

    from optionsjourney.bots import validation
    from optionsjourney.services import bot_store

    problem = bot_store.profit_problem(form, position)
    if problem:
        return _refused(problem)
    use, actions = bot_store.profits(form, position)
    with EXITS_LOCK:
        side, bot, group, done = _profit_side(deps, run_id, position)
        if side is None:
            return _refused(done)
        changed = replace(group, use_profit_actions=use, profit_actions=actions)
        problem = validation._profit_actions(changed)
        if problem:
            return _refused(_sentence(problem))
        kept = {a.sig for a in actions}
        if done - kept:
            return _refused(
                "A profit action on this group already ran: keep it in the list as it was "
                "(you can add to the list or change the ones still to come)."
            )
        _save(deps, run_id, position, "profit actions: this trade's", "saved",
              use_profit_actions=use,
              profit_actions=[bot_store.profit_row(a) for a in actions])  # fmt: skip
        _clear_profit_streaks(run_id, position)
    if not use or not actions:
        return Outcome(True, "This trade runs no profit actions on this group now.")
    unit = group.target_unit
    listed = "; ".join(a.label(unit) for a in actions)
    return Outcome(True, f"This trade's profit actions on this group: {listed}.")


def revert_profit_actions(deps, run_id: int, position: int) -> Outcome:
    """Back to the group's own profit actions for this trade, refused once one has run
    on the group (Tracker2: "a profit action has already fired on this group; edit the
    fields instead of reverting")."""
    with EXITS_LOCK:
        side, bot, group, done = _profit_side(deps, run_id, position)
        if side is None:
            return _refused(done)
        if done:
            return _refused(
                "A profit action has already run on this group: change the list instead of "
                "going back to the bot's."
            )
        _save(deps, run_id, position, "profit actions: the bot's", "reverted",
              use_profit_actions=None, profit_actions=None)  # fmt: skip
        _clear_profit_streaks(run_id, position)
    return Outcome(True, "This trade runs the group's own profit actions again.")


STOP_FILLED = "The stop filled first"
NOT_PROTECTED = (
    "It has no stop and no target now: it is not protected from a loss. Its timed close "
    "and watch rules still close it, as in Tracker2. Turn them back on from the run page."
)


def exits_off(deps, run_id: int, position: int) -> Outcome:
    """Turn one side's exits off (Tracker2's stop_loss_off and profit_target_off together):
    its target disarmed, its stop cancelled once the cancel is confirmed. Anything
    short of a confirmed cancel turns nothing off (management's abort rule: two live
    stops are worse than one stale one; a stop that isn't cancelled stays on)."""
    return _switch_off(deps, run_id, position, stop=True, target=True)


def stop_off(deps, run_id: int, position: int) -> Outcome:
    """Switch only the stop off (bots slice 18; management._apply_override_locked with
    stop_loss_off): slice 6's steps for the stop alone. The target stays armed."""
    return _switch_off(deps, run_id, position, stop=True, target=False)


def target_off(deps, run_id: int, position: int) -> Outcome:
    """Switch only the target off (profit_target_off): the armed target is disarmed.
    Nothing is at the broker for it, so nothing is sent; the stop stays working. Bots
    slice 21: a futures trade's resting target is cancelled at TastyTrade first; decision
    0048: so is any group's that rests."""
    if _rests_target(deps, run_id, position):
        return _resting_target_off(deps, run_id, position)
    return _switch_off(deps, run_id, position, stop=False, target=True)


STOP_ONLY_OFF = (
    "It has no stop now: nothing closes it on a loss. Its target still works. Switch the "
    "stop back on from the run page."
)
TARGET_ONLY_OFF = "Its stop still works. Switch the target back on from the run page."
NO_EXIT_LEFT = (
    "It has no stop, so with its target off it has no exit at all: nothing closes it. Switch "
    "the target back on, or close it."
)


def _switch_off(deps, run_id: int, position: int, *, stop: bool, target: bool) -> Outcome:
    if _futures_run(deps, run_id):
        return _refused(FUTURES_NOT_YET)
    both = stop and target
    with EXITS_LOCK:
        if both and _rests_target(deps, run_id, position):
            # Decision 0048: the resting target first, cancelled at TastyTrade and read back.
            # A resting target means no stop at the broker (the app holds it), so no stop
            # cancel below can fail after this pull.
            with deps.session_factory() as session:
                side = _read(session, run_id, position)
            if (_not_open(side) is None and not side.off
                    and side.latest("target", "working", "pending") is not None):  # fmt: skip
                off, note = bot_exits.cancel_resting_target(deps, run_id, position)
                if not off:
                    return _refused(f"{_sentence(note)} Exits are still on.")
        with deps.session_factory() as session:
            side = _read(session, run_id, position)
            why = _not_open(side)
            if why:
                return _refused(why)
            if both and side.off:
                return _refused("Exits are already off for this group.")
            if not both and stop and side.stop_off:
                return _refused("The stop is already off for this group.")
            if not both and target and side.target_off:
                return _refused("The target is already off for this group.")
            live = side.latest("stop", *bot_exits.STOP_LIVE) if stop else None
            target_ids = []
            if target:
                for row in side.rows:
                    if row.kind == "target" and row.state == "armed":
                        row.state = "cancelled"
                        bot_exits._breaches.pop(row.id, None)
                        bot_exits._log(row, deps, event=EXITS_OFF)
                        target_ids.append(row.id)
            stop_id = live.id if live is not None else None
            order_id = live.broker_order_id if live is not None else None
            if live is not None:
                was = live.state
                live.state = "cancelled"  # before any cancel: the monitor never re-arms it
                if was in ("pending", bot_exits.WATCHING) or not order_id:
                    live.broker_order_id, order_id = None, None  # nothing at the broker
                    bot_exits._log(live, deps, event=EXITS_OFF)
                else:
                    # Recovery's marker; `with_target`: whether this took the target too,
                    # so a cut-off puts back only what this action took (bots slice 18).
                    bot_exits._log(live, deps, event=TURNING_OFF, with_target=target)
            fields = {"stop_off": True} if stop else {}
            fields |= {"target_off": True} if target else {}
            what = _switch_words(stop, target, "off")
            _upsert(session, deps, run_id, position, what, "turning off", **fields)
            session.commit()
        if order_id:
            settled = _cancel_for_off(deps, run_id, stop_id, order_id)
            if settled is not None:
                filled = settled.startswith(STOP_FILLED)
                _undo_off(deps, run_id, position, stop_id, target_ids, settled, filled,
                          target=target)  # fmt: skip
                return _refused(settled.replace(", so exits are still on", _still_on(target)))
    _save(deps, run_id, position, _switch_words(stop, target, "off"), "off")
    if both:
        return Outcome(True, f"Exits are off for this group. {NOT_PROTECTED}")
    if stop:
        return Outcome(True, f"The stop is off for this group. {STOP_ONLY_OFF}")
    return Outcome(True, f"The target is off for this group. {TARGET_ONLY_OFF}")


def _switch_words(stop: bool, target: bool, way: str) -> str:
    if stop and target:
        return f"exits {way}"
    return f"{'stop' if stop else 'target'} {way}"


def _still_on(target: bool) -> str:
    return ", so exits are still on" if target else ", so the stop is still on"


def _cancel_for_off(deps, run_id: int, stop_id: int, order_id: str) -> str | None:
    """Cancel the stop for exits off and read it back. None once it's confirmed
    cancelled (the stop's event becomes EXITS_OFF); otherwise why exits stay on."""
    run, bot = bot_exits._run_and_bot(deps, run_id)
    sent = orders.cancel(
        deps.session_factory, deps.clients(), run.broker, run.account, order_id, "bot", bot.id,
        now=deps.now(), notify=deps.notify,
    )  # fmt: skip
    if sent.action == "refuse":
        return f"The stop's cancel wasn't sent ({sent.message}), so exits are still on."
    try:
        found = read_after_cancel(
            deps, lambda: bot_exits._broker(deps, run).get_order(run.account, order_id)
        )
    except Exception:  # noqa: BLE001 -- can't tell: it stays on, for the monitor to read
        logger.exception("couldn't read stop %s back for exits off", stop_id)
        return "The stop couldn't be read back after its cancel, so exits are still on."
    if found.status is OrderStatus.FILLED:
        return f"{STOP_FILLED}: the group is closing by its stop, so exits are still on."
    if found.may_be_working:
        return "The stop's cancel wasn't confirmed, so exits are still on."
    with deps.session_factory() as session:
        stop = session.get(BotExit, stop_id)
        bot_exits._log(stop, deps, event=EXITS_OFF)
        session.commit()
    return None


def _undo_off(deps, run_id, position, stop_id, target_ids, why: str, filled=False,
              target: bool = True) -> None:  # fmt: skip
    """Exits stay on: the stop goes back to working (the monitor reads it: a fill is
    handled, a cancelled one re-armed), the targets armed again (not when the stop
    filled: the group is closing by its stop, _stop_filled's to settle), the switch off.
    `target`: whether the action took the target too; if not, the target's switch is
    left as it was (bots slice 18: a target you switched off on its own stays off)."""
    if filled or not target:
        target_ids = []
    with deps.session_factory() as session:
        if stop_id is not None:
            stop = session.get(BotExit, stop_id)
            stop.state = "working" if stop.broker_order_id else "pending"
            bot_exits._log(stop, deps, event=f"exits stay on: {why}")
        for target_id in target_ids:
            row = session.get(BotExit, target_id)
            row.state = "armed"
            bot_exits._log(row, deps, event="armed again: exits stay on")
        fields = {"stop_off": False, "target_off": False} if target else {"stop_off": False}
        _upsert(session, deps, run_id, position, _switch_words(True, target, "off"), why,
                **fields)  # fmt: skip
        session.commit()


def exits_on(deps, run_id: int, position: int, text: str = "") -> Outcome:
    """Turn one side's exits back on: its stop placed again at its old trigger, or at
    the % you typed, after the same live-ask check as a moved stop (a stop that would
    fire at once, or with no fresh price, is refused and exits stay off); its target
    armed again."""
    return _switch_on(deps, run_id, position, text, stop=True, target=True)


def stop_on(deps, run_id: int, position: int, text: str = "") -> Outcome:
    """Switch the stop back on (bots slice 18): exits_on's stop half. The target's
    switch is left as it is."""
    return _switch_on(deps, run_id, position, text, stop=True, target=False)


def target_on(deps, run_id: int, position: int) -> Outcome:
    """Switch the target back on: the target you switched off is armed again, sized to
    what's open. Nothing is sent (the app watches it). Bots slice 21: a futures trade's is
    placed again at TastyTrade; decision 0048: so is any group's that rests."""
    if _rests_target(deps, run_id, position):
        return _resting_target_on(deps, run_id, position)
    return _switch_on(deps, run_id, position, "", stop=False, target=True)


# --- Bots slice 21, decision 0048: a resting target ----------------------------------------

FUTURES_NO_EXIT = (
    "This futures trade has no stop, so with its target off it has no exit at all: nothing "
    "closes it. Switch the target back on, or close it at TastyTrade."
)


def _resting_side(deps, run_id: int, position: int):
    """A group whose target rests at TastyTrade (a futures run's, or chosen: decision 0048),
    read: (side, None), or (None, why it can't be changed now)."""
    with deps.session_factory() as session:
        side = _read(session, run_id, position)
    why = _not_open(side)
    if why:
        return None, why
    if _maybe_live(side):
        return None, (
            "A change to this group's target may still be working at your broker: check your "
            "broker first. Nothing was sent."
        )
    return side, None


def _resting_change_target(deps, run_id: int, position: int, text: str) -> Outcome:
    """A group whose target rests at TastyTrade (a futures run's, or chosen: decision 0048):
    its resting target moved to the % of its credit you typed (Tracker2's
    apply_override with profit_target_pct on a PT-only run): the old one cancelled and
    confirmed, then the new one placed (bot_exits.change_resting_target)."""
    from dataclasses import replace

    pct, problem = manage.read_pct(text, _target_range(deps, run_id), "target")
    if problem:
        return _refused(problem)
    side, why = _resting_side(deps, run_id, position)
    if side is None:
        return _refused(why)
    if side.target_off:
        return _refused("The target is off for this group. Switch it back on first.")
    _, bot = bot_exits._run_and_bot(deps, run_id)
    group = _group(bot, position)
    if group is None or side.group.entry is None:
        return _refused("This group's entry price isn't known, so a target can't be worked out.")
    limit, problem = stops.target_limit(
        replace(group, target_unit="pct", target_pct=pct), side.group.entry
    )
    if problem:
        return _refused(_sentence(problem))
    moved, note = bot_exits.change_resting_target(deps, run_id, position, limit)
    if moved:
        _save(deps, run_id, position, f"target {manage._num(pct)}%", note, target_pct=pct)
    return Outcome(moved, _sentence(note))


def _resting_target_off(deps, run_id: int, position: int) -> Outcome:
    """A group whose target rests at TastyTrade (a futures run's, or chosen: decision 0048):
    its target cancelled there and read back (bot_exits.cancel_resting_target)."""
    side, why = _resting_side(deps, run_id, position)
    if side is None:
        return _refused(why)
    if side.target_off:
        return _refused("The target is already off for this group.")
    off, note = bot_exits.cancel_resting_target(deps, run_id, position)
    if not off:
        return _refused(_sentence(note))
    _save(deps, run_id, position, "target off", note, target_off=True)
    if _futures_run(deps, run_id):
        return Outcome(True, f"The target is off for this group: {note}. {FUTURES_NO_EXIT}")
    if side.latest("stop", *bot_exits.STOP_LIVE) is None:  # a group with only a target
        return Outcome(True, f"The target is off for this group: {note}. {NO_EXIT_LEFT}")
    return Outcome(True, f"The target is off for this group: {note}. {TARGET_ONLY_OFF}")


def _resting_target_on(deps, run_id: int, position: int) -> Outcome:
    """A group whose target rests at TastyTrade (a futures run's, or chosen: decision 0048):
    its target placed there again (bot_exits.place_target_again)."""
    side, why = _resting_side(deps, run_id, position)
    if side is None:
        return _refused(why)
    if not side.target_off:
        return _refused("The target is already on for this group.")
    placed, note = bot_exits.place_target_again(deps, run_id, position, side.size)
    if not placed:
        return _refused(f"{_sentence(note)} The target stays off.")
    _save(deps, run_id, position, "target on", note, target_off=False)
    return Outcome(True, f"The target is back on for this group: {note}.")


def _switch_on(deps, run_id: int, position: int, text: str, *, stop: bool,
               target: bool) -> Outcome:  # fmt: skip
    if _futures_run(deps, run_id):
        return _refused(FUTURES_NOT_YET)
    both = stop and target
    stays = "Exits stay off" if both else "The stop stays off"
    pct = None
    if stop and text and text.strip():
        pct, problem = manage.read_pct(text, manage.STOP_RANGE, "stop")
        if problem:
            return _refused(problem)
    with EXITS_LOCK:
        with deps.session_factory() as session:
            side = _read(session, run_id, position)
            why = _not_open(side)
            if why:
                return _refused(why)
            if both and not side.off:
                return _refused("Exits are already on for this group.")
            if not both and stop and not side.stop_off:
                return _refused("The stop is already on for this group.")
            if not both and target and not side.target_off:
                return _refused("The target is already on for this group.")
            if stop and _maybe_live(side):
                return _refused(MAYBE_LIVE)
            row = side.latest("stop") if stop else None
            if row is not None and row.state not in ("cancelled", "failed"):
                row = None
            if row is None and pct is not None:
                return _refused(
                    "This group has no stop to put back. Leave the stop % blank to switch it "
                    f"back on. {stays}."
                )
            stop_id, trigger = (row.id, row.trigger) if row is not None else (None, None)
            old_trigger = trigger
        run, bot = bot_exits._run_and_bot(deps, run_id)
        group, sides = _group(bot, position), side.sides()
        if stop_id is not None:
            ask, pnl = _live_check(deps, side)
            if ask is None and pnl is None:
                return _refused(
                    f"There's no fresh price for {_no_price(side)}, so the stop can't be "
                    f"checked. {stays}; try again in a moment."
                )
            calc_entry = None
            if pct is not None:
                found, problem = bot_exits._moved_calc(group, pct, sides, side.entry_credit, "you")
                if problem:
                    return _refused(f"{_sentence(problem)} {stays}.")
                trigger, calc_entry = found[0].trigger, found[1]
            if trigger is None:
                return _refused(f"The old stop has no level: type a new stop %. {stays}.")
            if side.anchor is None:  # a P&L level the app watches
                problem = bot_exits.fires_at_once(trigger, pnl)
                if problem is not None:
                    return _refused(
                        f"{_sentence(problem)} {stays}: set a wider stop, or close the group."
                    )
            elif trigger <= ask:
                return _refused(
                    f"A stop at {trigger} is at or below the short's ask {ask}, so it would fire "
                    f"at once. {stays}: set a higher stop, or close the group."
                )
        what = _switch_words(stop, target, "on")
        fields = {"stop_off": False} if stop else {}
        fields |= {"target_off": False} if target else {}
        with deps.session_factory() as session:
            _upsert(session, deps, run_id, position, what, "turning on", **fields)
            for row in (
                ()
                if not target
                else session.scalars(
                    select(BotExit).where(
                        BotExit.run_id == run_id,
                        BotExit.group_position == position,
                        BotExit.kind == "target",
                        BotExit.state == "cancelled",
                    )
                )
            ):
                if bot_exits.rested(row):  # decision 0048: placed again below, never armed
                    continue
                if row.log and row.log[-1].get("event") == EXITS_OFF:
                    row.state, row.contracts = "armed", min(row.contracts, side.size)
                    bot_exits._log(row, deps, event="armed again: exits back on")
            if stop_id is not None:
                # With the switch, in one transaction: a crash after this leaves a
                # "pending" stop at its level for the monitor to place, never exits on
                # with no stop on its way.
                row = session.get(BotExit, stop_id)
                row.contracts = min(row.contracts, side.size)  # never more than is left open
                row.trigger, row.state, row.broker_order_id = trigger, "pending", None
                if calc_entry is not None:
                    bot_exits._log(row, deps, event="moved by you", **calc_entry)
                else:
                    bot_exits._log(row, deps, event="exits back on")
            session.commit()
        if not stop:
            note = "its target is armed again"
            placed = True
        elif stop_id is None:
            note = "this group has no stop"
            placed = True
        else:
            placed, note = bot_exits._place_pending(deps, stop_id, old_trigger, trigger)
            if placed and pct is None:
                note = f"stop at {trigger}"
        fields = {"stop_pct": pct} if pct is not None and placed else {}
        if fields:  # bots slice 21: your % is the latest change: the trade's own style goes
            fields |= dict.fromkeys(bot_exits.STOP_STYLE)
        _save(deps, run_id, position, what, note, **fields)
        missed = ""
        if target and _rests_target(deps, run_id, position):
            # Decision 0048: the resting target placed again at TastyTrade (P4: never armed)
            placed_target, target_note = bot_exits.place_target_again(
                deps, run_id, position, side.size
            )
            if not placed_target:
                missed = f" The target wasn't placed again: {target_note}."
                _target_not_back(deps, run_id, position, target_note)
    name = "Exits are" if both else ("The stop is" if stop else "The target is")
    if placed:
        return Outcome(True, f"{name} back on for this group: {note}.{missed}")
    return Outcome(False, f"{name} back on, but {note}.{missed}")


def _target_not_back(deps, run_id: int, position: int, why: str) -> None:
    """A resting target Exits on couldn't place again: Needs you, alerted (as the module's
    other placement failures)."""
    from optionsjourney.services import bot_entry

    _, bot = bot_exits._run_and_bot(deps, run_id)
    detail = (
        f"{bot_exits._group_name(bot, position)}: exits are back on, but its profit target "
        f"wasn't placed again at {bot_exits.run_broker_name(deps, run_id)} ({why}). Check "
        "your broker; Restore missing exits on the run page places it again."
    )
    bot_entry.update_run(deps, run_id, reason=f"{bot_entry.NEEDS_YOU}: {detail}")
    bot_exits._alert(deps, f"{bot.name}: profit target not placed", detail, bot.id,
                     run_id=run_id)  # fmt: skip


def recover(deps) -> None:
    """At startup and each monitor pass: a stop left mid-way to "exits off" (the app
    died between marking it and confirming its cancel; TURNING_OFF is its last event)
    is read back. Gone: exits are off, as you asked. Still working, filled or
    unreadable: it goes back to working and exits stay on. Either way it alerts.
    Never raises."""
    try:
        with EXITS_LOCK:
            with deps.session_factory() as session:
                stuck = [
                    # with_target: whether that action took the target too (slice 18; a
                    # slice 6 marker without it was "exits off": both)
                    (r.id, r.run_id, r.group_position, r.broker_order_id,
                     bool(r.log[-1].get("with_target", True)))
                    for r in session.scalars(
                        select(BotExit).where(BotExit.kind == "stop", BotExit.state == "cancelled")
                    )
                    if r.log and r.log[-1].get("event") == TURNING_OFF
                ]  # fmt: skip
            for stop_id, run_id, position, order_id, with_target in stuck:
                try:
                    _recover_one(deps, stop_id, run_id, position, order_id, with_target)
                except Exception:  # noqa: BLE001 -- one stop never stops the others
                    logger.exception("couldn't recover stop %s after a cut-off exits off", stop_id)
    except Exception:  # noqa: BLE001 -- the monitor's other steps go on
        logger.exception("exits-off recovery failed")


def _recover_one(deps, stop_id, run_id, position, order_id, with_target=True) -> None:
    run, bot = bot_exits._run_and_bot(deps, run_id)
    try:
        found = bot_exits._broker(deps, run).get_order(run.account, order_id)
        gone = found.status is not OrderStatus.FILLED and not found.may_be_working
    except Exception:  # noqa: BLE001 -- can't tell: it stays on
        logger.exception("couldn't read stop %s after a cut-off exits off", stop_id)
        gone = False
    if gone:
        with deps.session_factory() as session:
            bot_exits._log(session.get(BotExit, stop_id), deps, event=EXITS_OFF)
            session.commit()
        tail = (
            f"Its stop was cancelled, so exits are off for that group. {NOT_PROTECTED}"
            if with_target
            else f"Its stop was cancelled, so the stop is off for that group. {STOP_ONLY_OFF}"
        )
    else:
        with deps.session_factory() as session:
            targets = [
                r.id
                for r in session.scalars(
                    select(BotExit).where(
                        BotExit.run_id == run_id,
                        BotExit.group_position == position,
                        BotExit.kind == "target",
                        BotExit.state == "cancelled",
                    )
                )
                if r.log and r.log[-1].get("event") == EXITS_OFF
            ]
        _undo_off(deps, run_id, position, stop_id, targets, "turning exits off was cut off",
                  target=with_target)  # fmt: skip
        tail = "Its stop is still there (or couldn't be read), so it stays on."
    bot_exits._alert(
        deps, f"{bot.name}: turning exits off was cut off",
        f"Turning a group's exits off was cut off part-way. {tail} Check your broker.", bot.id,
        run_id=run_id,
    )  # fmt: skip


def _held_units(positions: dict[str, int], legs) -> int:
    """The whole units of the group every leg holds the right way at the broker: each
    leg's count in its own direction over its ratio, the smallest of them
    (close_jobs.select_close_legs)."""
    units = []
    for leg in legs:
        held = positions.get(plain(leg.symbol), 0)
        held_dir = held if leg.side is Side.BUY else -held
        units.append(max(held_dir, 0) // leg.ratio)
    return min(units, default=0)


ORDER_TYPES = ("limit", "market")  # manual_close.ORDER_TYPES
MARKET_0DTE = (
    "Market is for 0DTE trades only (every leg expiring today), as in Tracker2. Use Limit: "
    "it starts from the mid and chases."
)


def _order_type(text: str | None) -> tuple[str | None, str | None]:
    kind = (text or "limit").strip().lower()
    if kind not in ORDER_TYPES:
        return None, "The order type must be Limit or Market."
    return kind, None


def _how(kind: str, legs) -> str:
    if kind != "market":
        return "from the mid"
    if len(legs) == 1:
        return "at market"
    # manual_close._settings: only a one-leg group goes straight to market
    return "by the bot's exit chase (a group of more than one leg is never sent at market)"


def _can_close(deps, run_id: int, kind: str) -> str | None:
    """Why no close you ask for may start on this run now, or None (manual_close's
    "Refused before anything is touched")."""
    if _futures_run(deps, run_id):
        return FUTURES_NOT_YET
    if not bot_exits._session_open(deps):
        return "You can close only while the market is open (its regular session)."
    if bot_exits._frozen(deps):
        return "Orders are frozen (Freeze is on), so nothing can be closed now."
    if kind == "market" and not _run_0dte(deps, run_id):
        return MARKET_0DTE
    return None


def _run_0dte(deps, run_id: int) -> bool:
    from optionsjourney.core.market import MARKET_TZ

    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        legs = list(run.legs or []) if run is not None else []
    return _zero_dte(legs, deps.now().astimezone(MARKET_TZ).date())


def close_part(deps, run_id: int, position: int, text: str, order_type: str = "limit") -> Outcome:
    """Close `text` units of one group (manual_close.begin_manual_close): refused
    before anything is touched outside the regular session, under Freeze, on a run
    that never traded live, on a closed or busy group, or for more than is held (the
    whole units every leg holds at the broker, capped at the group's open units).
    Then bot_exits.fire_close: claimed like a timed close, closed in the background,
    the rest protected again. Bots slice 18: `order_type` Limit (from the mid, then the
    chase) or Market (0DTE only; straight to market on a one-leg group)."""
    kind, problem = _order_type(order_type)
    if problem:
        return _refused(problem)
    why = _can_close(deps, run_id, kind)
    if why:
        return _refused(why)
    with deps.session_factory() as session:
        side = _read(session, run_id, position)
        why = _not_open(side)
        if why:
            return _refused(why)
        if not side.run.live:
            return _refused(_nothing_to_close(side.run))
        if _maybe_live(side):
            return _refused(MAYBE_LIVE)
    run, _ = bot_exits._run_and_bot(deps, run_id)
    try:
        positions = bot_exits._positions(deps, run)
    except Exception:  # noqa: BLE001 -- never size a close on a guess
        logger.exception("couldn't read positions to close run %s side %s", run_id, position)
        return _refused("Couldn't read your positions from the broker, so nothing was sent.")
    held = min(_held_units(positions, side.legs), side.size)
    quantity, problem = manage.quantity_problem(text, held)
    if problem:
        return _refused(problem)
    if bot_exits.fire_close(deps, run_id, position, quantity, side.size,
                            order_type=kind) is None:  # fmt: skip
        return _refused(
            "This group couldn't be claimed: a close is running on it, or it just closed. "
            "Nothing was sent."
        )
    return Outcome(
        True,
        f"Closing {quantity} of {held} on this group {_how(kind, side.legs)}, in the "
        f"background.{_rest_note(side)}",
    )


def _rest_note(side: _Side) -> str:
    if side.off:
        return " Exits are off for this group: what's left is not protected."
    if side.stop_off:
        return " The stop is off for this group: what's left has no stop."
    return " The stop and target come back for whatever is left."


def close_trade(deps, run_id: int, order_type: str = "limit",
                only: set[int] | None = None) -> Outcome:  # fmt: skip
    """Close entire trade (bots slice 18; manual_close.begin_manual_close with no group,
    then _execute_plan): every open group, each its own open units (a full close,
    contracts_by_group), one after another in one background job. Refused before
    anything is touched as close_part is, and when any open group is busy or held back,
    or nothing of it is held. Each group is claimed only when its turn comes, so a group
    not reached keeps its stop and target; the job stops at the first group that doesn't
    fully close and says which were not closed. `only`: just these groups (one side of
    a split iron condor, from its journal row)."""
    kind, problem = _order_type(order_type)
    if problem:
        return _refused(problem)
    why = _can_close(deps, run_id, kind)
    if why:
        return _refused(why)
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or run.status not in HOLDING or not run.filled_legs:
            return _refused("This trade isn't open.")
        if not run.live:
            return _refused(_nothing_to_close(run))
        sides = [
            (position, side)
            for position in sorted({leg["group"] for leg in run.legs})
            if (only is None or position in only)
            and (side := _read(session, run_id, position)) is not None and side.state != "closed"
        ]
    if not sides:
        return _refused("Nothing of this trade is still open.")
    if any(side.state == "busy" for _, side in sides):
        return _refused(
            "A close is running on one of its groups (or its exits are being placed). Try "
            "again when it's done. Nothing was sent."
        )
    if any(_maybe_live(side) for _, side in sides):
        return _refused(MAYBE_LIVE)
    trade_run, bot = bot_exits._run_and_bot(deps, run_id)
    try:
        positions = bot_exits._positions(deps, trade_run)
    except Exception:  # noqa: BLE001 -- never size a close on a guess
        logger.exception("couldn't read positions to close run %s", run_id)
        return _refused("Couldn't read your positions from the broker, so nothing was sent.")
    plan = []
    for position, side in sides:
        held = min(_held_units(positions, side.legs), side.size)
        if held >= 1:  # manual_close._open_groups: a group already flat is left out
            plan.append((position, held, side.size))
    if not plan:
        return _refused("Your broker shows nothing of this trade held, so nothing was sent.")
    try:
        deps.background(_close_trade_job, deps, run_id, plan, kind)
    except Exception:  # noqa: BLE001 -- nothing was claimed yet: nothing to put back
        logger.exception("couldn't start closing run %s", run_id)
        return _refused("The close couldn't be started. Nothing was sent.")
    names = ", ".join(bot_exits._group_name(bot, position) for position, _, _ in plan)
    return Outcome(
        True,
        f"Closing {'this side' if only is not None else 'the whole trade'} in the background, "
        f"one group at a time ({names}), "
        f"{'at market where a group is one leg' if kind == 'market' else 'from the mid'}.",
    )


def _close_trade_job(deps, run_id: int, plan, kind: str) -> None:
    """Close entire trade's job (manual_close._execute_plan): each group claimed and
    closed on this thread in turn; it stops at the first that doesn't fully close, and
    the groups not reached keep their stop and target. Never raises."""
    run, bot = bot_exits._run_and_bot(deps, run_id)
    done = []
    try:
        for i, (position, _, _) in enumerate(plan):
            name = bot_exits._group_name(bot, position)
            # Read again just before its claim: an earlier group's close (or you, at the
            # broker) may have changed what's held and open since the plan was made.
            with deps.session_factory() as session:
                side = _read(session, run_id, position)
            if side is None or side.state == "closed":
                done.append(f"{name} (already closed)")
                continue
            try:
                held = min(_held_units(bot_exits._positions(deps, run), side.legs), side.size)
            except Exception:  # noqa: BLE001 -- never size a close on a guess
                logger.exception("couldn't read positions to close run %s group %s", run_id,
                                 position)  # fmt: skip
                held = None
            if held is not None and held < 1:
                done.append(f"{name} (nothing held)")
                continue
            exit_id = None if held is None else bot_exits.fire_close(
                deps, run_id, position, held, side.size, order_type=kind, wait=True,
            )  # fmt: skip
            state = None
            if exit_id is not None:
                with deps.session_factory() as session:
                    state = session.get(BotExit, exit_id).state
            if state not in ("filled", "expired"):
                rest = [bot_exits._group_name(bot, p) for p, _, _ in plan[i + 1 :]]
                if held is None:
                    what = "couldn't be sized: your positions couldn't be read, so nothing was sent"
                elif exit_id is None:
                    what = "couldn't be claimed (a close is running on it, or it just closed)"
                else:
                    what = f"didn't fully close ({state})"
                tail = (
                    f" {', '.join(rest)} {'was' if len(rest) == 1 else 'were'} NOT closed and "
                    "keep their stop and target."
                    if rest
                    else ""
                )
                closed = f" Closed first: {', '.join(done)}." if done else ""
                bot_exits._alert(
                    deps, f"{bot.name}: Close entire trade stopped",
                    f"Closing the whole trade stopped at {name}: it {what}.{closed}{tail} "
                    "Check your broker.", bot.id, run_id=run_id,
                )  # fmt: skip
                return
            done.append(name)
        bot_exits._alert(
            deps, f"{bot.name}: whole trade closed", f"Closed {', '.join(done)}.", bot.id,
            run_id=run_id, routine=True,
        )  # fmt: skip
    except Exception:  # noqa: BLE001 -- never silent
        logger.exception("closing run %s's whole trade failed", run_id)
        bot_exits._alert(
            deps, f"{bot.name}: Close entire trade stopped",
            "Closing the whole trade went wrong part-way. Check the run page and your broker.",
            bot.id, run_id=run_id,
        )  # fmt: skip


MAX_CLOSE_SELECTED = 15  # automation_bp.MAX_CLOSE_SELECTED


def close_selected(deps, run_ids, order_type: str = "limit") -> list[tuple[int, Outcome]]:
    """Close selected (automation_bp.api_close_selected): every ticked 0DTE trade
    closed in full, each as its own Close entire trade. One refusal never blocks the
    rest."""
    ids = []
    for raw in run_ids:
        try:
            ids.append(int(raw))
        except TypeError, ValueError:
            continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        return [(0, _refused("Tick at least one trade to close."))]
    if len(ids) > MAX_CLOSE_SELECTED:
        return [(0, _refused(f"Close selected takes at most {MAX_CLOSE_SELECTED} trades."))]
    out = []
    for run_id in ids:
        try:
            if not _run_0dte(deps, run_id):
                out.append((run_id, _refused("Close selected is for 0DTE trades only.")))
                continue
            out.append((run_id, close_trade(deps, run_id, order_type)))
        except Exception:  # noqa: BLE001 -- one trade never stops the others
            logger.exception("close selected: run %s", run_id)
            out.append((run_id, Outcome(False, CHECK_BROKER)))
    return out


# --- What the pages show ----------------------------------------------------------------


@dataclass(frozen=True)
class SideView:
    """One side of a run, for the run page's Manage this trade box."""

    position: int
    name: str
    state: str  # open, busy or closed (timing.side_state); "selling": closed, but its
    # leg was handed to a wing order that hasn't filled yet (#179): still held
    off: bool
    stop: Decimal | None  # the working (or pending) stop's trigger
    stop_state: str | None
    target: Decimal | None  # the armed target's close-at cost
    size: int  # what's open by the app's records
    chips: tuple
    held: bool = False  # an earlier order may still be live: nothing new is sent
    watched: bool = False  # the app watches the group's stop on its P&L (bots slice 13)
    futures: bool = False  # bots slice 17: a profit target resting at the broker, no stop
    target_order: str | None = None  # bots slice 17: the resting target's order id
    stop_off: bool = False  # bots slice 18: the stop switched off
    target_off: bool = False  # the target switched off
    # Bots slice 21: the wing rule in force (None: the group has no rest after its stop),
    # and whether it's this trade's own
    wing_mode: str | None = None
    wing_limit: Decimal | None = None
    wing_own: bool = False
    # the closing-order overlay in force (None: the group can't have one), and whether
    # it's this trade's own
    co_mode: str | None = None
    co_amount: Decimal | None = None
    co_own: bool = False
    # the profit actions in force (use, rows, the group's target unit), and whether they're
    # this trade's own
    profit_use: bool = False
    profit_rows: tuple = ()
    profit_unit: str = "pct"
    profit_own: bool = False
    note: str | None = None  # decision 0048: what the fill changed for this group
    # #209: what `size` counts. A one-leg group's unit is one contract (its ratio over its
    # gcd), so a 3-lot short call is 3 and Close group closes 1 to 3 of them.
    unit: str = "unit"


def _closed_by_you(rows) -> int:
    return sum(
        int(entry.get("closed", 0))
        for r in rows
        if r.kind == "close" and r.state == "reduced"
        for entry in r.log
    )


def _overrides(session, run_ids) -> dict[tuple[int, int], manage.Override]:
    if not run_ids:
        return {}
    return {
        (o.run_id, o.group_position): manage.Override(
            o.stop_pct,
            o.target_pct,
            o.exits_off,
            bool(o.stop_off),
            bool(o.target_off),
            o.stop_unit,
            o.stop_usd,
            o.co_mode,
            o.co_amount,
            o.on_stop_remainder,
            o.remainder_limit,
            o.use_profit_actions is not None or o.profit_actions is not None,
        )
        for o in session.scalars(
            select(BotTradeOverride).where(BotTradeOverride.run_id.in_(list(run_ids)))
        )
    }


FUTURES_CHIP = manage.Chip(
    "futures", bot_exits.FUTURES_NOTE,
    "A futures trade: its profit target rests at TastyTrade and it has no stop (decision "
    "0043). Change or cancel it there.",
)  # fmt: skip


def _side_chips(bot, run: BotRun, position: int, rows, override) -> tuple:
    group = _group(bot, position) if bot is not None else None
    _, gcd = exit_math.group_legs(run.legs, position)
    model = next((r for r in rows if r.kind in ("stop", "target")), None)
    watched = group is not None and (
        bool(model.watched) if model is not None else bot.kind(position) == "watched"
    )
    lead = (FUTURES_CHIP,) if run.pt_only else ()  # Tracker2's override note, "futures"
    return lead + tuple(
        manage.chips(
            group, override, _closed_by_you(rows), run.contracts * gcd,
            bot.stop_co_mode if bot is not None else "off",
            bot.stop_co_amount if bot is not None else None,
            watched=watched,
        )
    )  # fmt: skip


def sides(session_factory, run_id: int, bot) -> list[SideView]:
    """Each side of a filled run, with what you can do to it and its chips."""
    with session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or run.status not in HOLDING or not run.filled_legs:
            return []
        overrides = _overrides(session, [run_id])
        out = []
        for position in sorted({leg["group"] for leg in run.legs}):
            side = _read(session, run_id, position)
            if side is None:
                continue
            stop = side.latest("stop", *bot_exits.STOP_LIVE)
            # a futures target rests at the broker (bots slice 17): working, or pending
            target = side.latest("target", "armed", "working", "pending")
            name = next(
                (g.name for g in bot.groups if g.position == position), f"Group {position + 1}"
            ) if bot is not None else f"Group {position + 1}"  # fmt: skip
            # decision 0048: what the fill changed for this group (exit_plan's note); a
            # futures target's note is FUTURES_NOTE, which the run page says already
            note = None if run.pt_only else next(
                (e["note"] for r in side.rows if r.kind in ("stop", "target")
                 for e in r.log or () if isinstance(e, dict) and e.get("note")), None,
            )  # fmt: skip
            override = overrides.get((run_id, position))
            wing = _wing_in_force(bot, run, position, side, override)
            co = _co_in_force(bot, run, side, override)
            mine = bot_exits.effective_group(session, run_id, bot, position) if bot else None
            profits = (
                (mine.use_profit_actions, mine.profit_actions, mine.target_unit,
                 bool(override and override.own_profit_actions))
                if mine is not None and not run.pt_only else (False, (), "pct", False)
            )  # fmt: skip
            selling = side.state == "closed" and any(
                r.kind == "wing" and r.state in bot_exits.STOP_LIVE + ("chasing",)
                for r in side.rows
            )  # the close handed its leg to a wing order that's still working (#179)
            out.append(SideView(
                position, name, "selling" if selling else side.state, side.off,
                stop.trigger if stop else None, stop.state if stop else None,
                target.limit if target else None, side.size,
                _side_chips(bot, run, position, side.rows, override),
                _maybe_live(side), side.watched, bool(run.pt_only),
                target.broker_order_id
                if target is not None and target.state in ("working", "pending") else None,
                side.stop_off, side.target_off, *wing, *co, *profits, note=note,
                unit="contract" if len(exit_math.group_legs(run.legs, position)[0]) == 1
                else "unit",
            ))  # fmt: skip
        return out


def _wing_in_force(bot, run: BotRun, position: int, side: _Side, override):
    """(mode, limit, this trade's own) for a group whose stop leaves legs behind, else
    (None, None, False)."""
    group = _group(bot, position) if bot is not None else None
    if group is None or run.pt_only or side.anchor is None or len(side.legs) < 2:
        return None, None, False
    if override is not None and override.on_stop_remainder is not None:
        return override.on_stop_remainder, override.remainder_limit, True
    return group.on_stop_remainder, group.remainder_limit, False


def _co_in_force(bot, run: BotRun, side: _Side, override):
    """(mode, amount, this trade's own) for a group whose stop is on its short in a trade
    that filled for a credit (decision 0048: by the fill), else (None, None, False)."""
    debit = side.entry_credit is not None and side.entry_credit < 0
    if bot is None or run.pt_only or side.anchor is None or debit:
        return None, None, False
    if override is not None and override.co_mode is not None:
        return override.co_mode, override.co_amount, True
    return bot.stop_co_mode, bot.stop_co_amount, False


def journal_chips(session_factory, entries) -> dict[str, tuple]:
    """{position key: chips} for the journal's bot trades that differ from their bot,
    read for the whole page at once (override_tags.tags_for_positions). A split
    condor's half shows only its own side's chips; with two sides both changed, each
    chip names its side."""
    linked = [e for e in entries
              if getattr(e, "bot", None) is not None and not getattr(e.bot, "matched", False)]
    return _chips(session_factory, [
        (e.position.key, e.bot.run_id, {plain(leg.symbol) for leg in e.position.legs})
        for e in linked
    ])  # fmt: skip


def chips_for(session_factory, run_id: int, symbols) -> tuple:
    """One journal row's chips (bots slice 20; Tracker2's api_run_tags): the run's sides
    holding one of `symbols`, as journal_chips gives them. () when nothing differs."""
    found = _chips(session_factory, [("row", run_id, {plain(s) for s in symbols})])
    return found.get("row", ())


def _chips(session_factory, linked: list[tuple[str, int, set]]) -> dict[str, tuple]:
    """{key: chips} for (key, run id, the row's plain symbols)."""
    from optionsjourney.services import bot_store

    if not linked:
        return {}
    run_ids = {run_id for _, run_id, _ in linked}
    with session_factory() as session:
        overrides = _overrides(session, run_ids)
        closes = {
            (r.run_id, r.group_position)
            for r in session.scalars(
                select(BotExit).where(
                    BotExit.run_id.in_(list(run_ids)),
                    BotExit.kind == "close",
                    BotExit.state == "reduced",
                )
            )
        }
        futures = set(
            session.scalars(
                select(BotRun.id).where(BotRun.id.in_(list(run_ids)), BotRun.pt_only.is_(True))
            )
        )  # bots slice 17: every futures trade says it has a profit target only
        wanted = {key[0] for key in overrides} | {key[0] for key in closes} | futures
        if not wanted:
            return {}
        runs = {r.id: r for r in session.scalars(select(BotRun).where(BotRun.id.in_(wanted)))}
        exits: dict[tuple[int, int], list] = {}
        for row in session.scalars(select(BotExit).where(BotExit.run_id.in_(list(wanted)))):
            exits.setdefault((row.run_id, row.group_position), []).append(row)
        bots = {}
        out = {}
        for key, run_id, symbols in linked:
            run = runs.get(run_id)
            if run is None:
                continue
            if run.bot_id not in bots:
                bots[run.bot_id] = bot_store.load(session_factory, run.bot_id)
            bot = bots[run.bot_id]
            per_side = []
            for position in sorted({leg["group"] for leg in run.legs}):
                own = [g for g in run.legs if g["group"] == position]
                if not any(plain(g["symbol"]) in symbols for g in own):
                    continue  # a split condor's other half
                chips = _side_chips(
                    bot, run, position, exits.get((run.id, position), []),
                    overrides.get((run.id, position)),
                )  # fmt: skip
                if chips:
                    name = _group(bot, position).name if bot and _group(bot, position) else ""
                    per_side.append((name, chips))
            if not per_side:
                continue
            many = len(per_side) > 1
            out[key] = tuple(
                manage.Chip(c.kind, f"{name} {c.label}" if many and name else c.label, c.title)
                for name, chips in per_side
                for c in chips
            )
        return out


def recheck(deps, run_id: int, position: int) -> Outcome:
    """The run page's "I've checked my broker: re-check now": the same check the
    monitor runs (bot_exits.settle_side), never a blind unlock. It sends nothing:
    released, you set the stop (or close) yourself."""
    with EXITS_LOCK:
        settled, message = bot_exits.settle_side(deps, run_id, position, asked=True)
    return Outcome(settled, message)


@dataclass(frozen=True)
class OpenTrade:
    """One held bot trade, for the Automation page's Open bot trades card (bots slice
    18): its open groups and whether it's 0DTE (every leg expires today), which is what
    Close selected and Market take."""

    run_id: int
    bot_id: int
    bot_name: str
    groups: tuple[str, ...]  # the open groups' names
    zero_dte: bool


def _zero_dte(legs, today) -> bool:
    from optionsjourney.bots import futures
    from optionsjourney.core.symbols import parse_occ

    days = []
    for leg in legs:
        parsed = parse_occ(leg["symbol"])
        option = parsed[1] if parsed is not None else futures.option_of(leg["symbol"])
        if option is None:
            return False
        days.append(option.expiration)
    return bool(days) and all(day == today for day in days)


def open_trades(session_factory, now) -> list[OpenTrade]:
    """Every held bot trade with a group still open, oldest first."""
    from optionsjourney.core.market import MARKET_TZ
    from optionsjourney.services import bot_rearm, bot_store

    today = now.astimezone(MARKET_TZ).date()
    out, bots = [], {}
    for run_id in bot_rearm.open_runs(session_factory, now):
        with session_factory() as session:
            run = session.get(BotRun, run_id)
            if run.bot_id not in bots:
                bots[run.bot_id] = bot_store.load(session_factory, run.bot_id)
            bot = bots[run.bot_id]
            names = []
            for position in sorted({leg["group"] for leg in run.legs}):
                side = _read(session, run_id, position)
                if side is not None and side.state != "closed":
                    names.append(bot_exits._group_name(bot, position))
            if names:
                out.append(OpenTrade(run_id, run.bot_id, bot.name if bot else "A bot",
                                     tuple(names), _zero_dte(run.legs, today)))  # fmt: skip
    return out


def _nothing_to_close(run) -> str:
    """Why a run that sent no real order can't be closed at the broker (final review M1:
    a paper trade is said as Paper, not preview)."""
    if run.paper:
        return "This is a paper trade, so there's nothing at the broker to close."
    return "This trade ran in preview, so there's nothing at the broker to close."
