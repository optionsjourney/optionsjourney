"""The timed close and time actions, run from the monitor loop every 30 s (spec:
bots slice 4; Tracker2's timed_close.fire_timed_closes and
time_actions.fire_time_actions, both called from its exit monitor).

Which runs and sides are due is worked out here, from bots/timing.py; what
happens to a side (the claim, the close, a moved stop or target) is bot_exits'."""

import logging
from collections.abc import Callable
from datetime import date

from sqlalchemy import select

from optionsjourney.bots import overrides, timing
from optionsjourney.bots.definition import BotDef
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import naive_et, session_close
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_entry, bot_exits, bot_store, bot_trade_settings

logger = logging.getLogger(__name__)
HOLDING = ("filled", "partial")
_FAILED: set[tuple[int, date]] = set()  # (run, day) whose timed close broke: alerted once
_WAITING: set[tuple[int, int, int]] = set()  # (run, row, side) waiting for a quote: alerted once
_BROKEN: set[tuple[int, int, int]] = set()  # (run, row, side) whose read broke: alerted once


def _et_day(moment) -> date:
    return moment.astimezone(MARKET_TZ).date()


def group_expirations(legs) -> dict[int, date]:
    """Each group's nearest expiration, from its legs' OCC symbols (bots slice 14,
    question 8: a group closes by its own days to expiration; Tracker2 counts a journal
    position's nearest open leg, timed_close.dte_for_position)."""
    by_group: dict[int, list[str]] = {}
    for leg in legs:
        by_group.setdefault(leg["group"], []).append(leg["symbol"])
    out = {}
    for position, symbols in by_group.items():
        nearest = timing.nearest_expiration(symbols)
        if nearest is not None:
            out[position] = nearest
    return out


def _open_runs(deps, wanted: Callable[[BotDef, int], bool], today: date):
    """(run id, bot, legs, fill day, each group's nearest expiration) for every run that
    may hold a position, that `wanted(bot, run id)` picks: filled or partly filled with fills,
    not still entering, with a group not expired before today (a group expiring today
    is kept, for the missed check). A group whose nearest expiration is before today
    has an expired leg: it's left out (its far legs are yours, services/bot_settle.py)."""
    with deps.session_factory() as session:
        found = [
            (r.id, r.bot_id, list(r.legs), r.filled_at)
            for r in session.scalars(
                select(BotRun).where(BotRun.status.in_(HOLDING), BotRun.paper.is_(False))
            )
            if r.filled_legs and r.legs
        ]
    bots: dict[int, BotDef | None] = {}
    for run_id, bot_id, legs, filled_at in found:
        if run_id in bot_entry.ACTIVE:
            continue
        if bot_id not in bots:
            bots[bot_id] = bot_store.load(deps.session_factory, bot_id)
        bot = bots[bot_id]
        if bot is None or not wanted(bot, run_id):
            continue
        expirations = {p: e for p, e in group_expirations(legs).items() if e >= today}
        if not expirations:
            continue
        yield run_id, bot, legs, (_et_day(filled_at) if filled_at else today), expirations


def _sides(deps, run_id: int) -> dict[int, list[BotExit]]:
    with deps.session_factory() as session:
        side: dict[int, list[BotExit]] = {}
        for row in session.scalars(select(BotExit).where(BotExit.run_id == run_id)):
            side.setdefault(row.group_position, []).append(row)
        return side


def _side_name(bot: BotDef, position: int) -> str:
    return next((g.name for g in bot.groups if g.position == position), f"Group {position + 1}")


def timed_pass(deps) -> None:
    """Fire each due side's timed close; once the session is over, mark the ones
    that never ran as missed and say so. Never raises."""
    local = naive_et(deps.now())
    today = local.date()
    if session_close(today) is None:
        return  # weekends and holidays: never (fire_timed_closes' first check)
    frozen = bot_exits._frozen(deps)
    # Bots slice 21 (timed_close.effective_settings): each run's own timed close first; a
    # run's row with no time switches the bot's off for that trade.
    own = bot_trade_settings.all_timed(deps.session_factory)

    def settings(bot, run_id):
        return overrides.effective_timed(bot, own.get(run_id))

    for run_id, bot, legs, _, expirations in _open_runs(
        deps, lambda b, run_id: settings(b, run_id)[0] is not None, today
    ):
        close_time, close_dte, _ = settings(bot, run_id)
        try:
            _timed_run(deps, run_id, bot, legs, expirations, local, frozen, close_time,
                       close_dte)  # fmt: skip
        except Exception:  # noqa: BLE001 -- one run never stops the others
            logger.exception("timed close: run %s failed", run_id)
            if (run_id, today) not in _FAILED:  # fire_timed_closes: alerted once a day
                _FAILED.add((run_id, today))
                bot_exits._alert(
                    deps,
                    f"{bot.name}: timed close failed",
                    "Something went wrong starting a timed close; the trade may still be "
                    "open. The app keeps trying. Check your broker.",
                    bot.id,
                )


def _timed_run(deps, run_id, bot: BotDef, legs, expirations, local, frozen, close_time: str,
               close_dte: int) -> None:  # fmt: skip
    due_by = {
        position: timing.timed_close_due(close_time, close_dte, expiration, local)
        for position, expiration in expirations.items()
    }
    if not any(due_by.values()):
        return
    today = local.date()
    note = f"{close_time}, {close_dte} DTE or fewer"
    sides = _sides(deps, run_id)
    # Bots slice 18, the author's ruling (Tracker2: timed_close.fire_timed_closes reads only
    # its own timed_close_overrides, never stop_loss_off/profit_target_off): switching a
    # side's stop and target off doesn't stop its timed close. It's said as it fires.
    off = bot_exits.stop_off_sides(deps.session_factory, run_id) | bot_exits.target_off_sides(
        deps.session_factory, run_id
    )
    missed = []
    for position in sorted(due_by):
        due = due_by[position]
        if due is None:
            continue  # this group isn't due today (slice 14: its own expiration)
        rows = sides.get(position, [])
        if any(r.kind == "timed" and _et_day(r.created_at) == today for r in rows):
            continue  # once per side per day, whatever happened (_fired_today)
        state = timing.side_state((r.kind, r.state) for r in rows)
        if due == "over":
            if state == "open":
                _missed(deps, run_id, position)
                missed.append(_side_name(bot, position))
            continue
        if state != "open" or frozen:
            continue  # closed; or busy or frozen: try again next pass
        if bot_exits.fire_timed(deps, run_id, position, note) is not None and position in off:
            bot_exits._alert(
                deps, f"{bot.name}: timed close on a side with exits off",
                f"{_side_name(bot, position)} has its stop or target switched off, and its "
                f"{close_time} timed close is closing it now, as Tracker2's does: switching "
                "exits off removes the stop and target, not the timed close.", bot.id,
                run_id=run_id,
            )  # fmt: skip
    if missed:
        bot_exits._alert(
            deps,
            f"{bot.name}: timed close missed",
            f"The {close_time} timed close didn't run (the app wasn't running, or Freeze "
            f"was on), so these sides weren't closed by time: {', '.join(missed)}. "
            "Check your broker.",
            bot.id,
            run_id=run_id,
        )


def _missed(deps, run_id: int, position: int) -> None:
    """fire_timed_closes' missed row: the session ended before the close ran. It names
    the group's legs as its stop and target do, sized in the group's open units."""
    now = deps.now()
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        rows = list(session.scalars(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.group_position == position)))  # fmt: skip
        session.add(
            BotExit(
                run_id=run_id,
                group_position=position,
                kind="timed",
                state="missed",
                **bot_exits.group_columns(session, run, position, rows),
                contracts=bot_exits.group_units(session, run, position),
                rearms=0,
                log=[{"at": now.isoformat(), "event": "missed: the session ended before it ran"}],
                created_at=now,
                updated_at=now,
            )  # fmt: skip
        )
        session.commit()


def actions_pass(deps) -> None:
    """Apply each due time action to each open side, once (fire_time_actions). Not
    while Freeze is on: moving a stop needs orders. Never raises."""
    local = naive_et(deps.now())
    today = local.date()
    if session_close(today) is None or bot_exits._frozen(deps):
        return
    for run_id, bot, legs, fill_day, expirations in _open_runs(
        deps, lambda b, _: b.use_time_actions and bool(b.time_actions), today
    ):
        try:
            _actions_run(deps, run_id, bot, legs, fill_day, expirations, local)
        except Exception:  # noqa: BLE001 -- one run never stops the others
            logger.exception("time actions: run %s failed", run_id)


def _actions_run(deps, run_id, bot: BotDef, legs, fill_day, expirations, local) -> None:
    for number, action in enumerate(bot.time_actions, 1):
        # Slice 14: a DTE row counts to each group's own nearest expiration
        due_at = {
            position: timing.action_due(action, expiration, fill_day, local)
            for position, expiration in sorted(expirations.items())
        }
        positions = [p for p, (due, _) in due_at.items() if due]
        if not positions:
            continue
        late = any(due_at[p][1] for p in positions)
        applied, problems, retrying, waiting = False, [], [], []
        for position in positions:
            try:
                state, notes = bot_exits.apply_time_action(
                    deps, run_id, position, action, number, due_at[position][1]
                )
            except Exception:  # noqa: BLE001 -- nothing moved or recorded: tried next pass
                logger.exception(
                    "time action %s on run %s side %s failed", number, run_id, position
                )
                if (run_id, number, position) not in _BROKEN:  # said once, not every pass
                    _BROKEN.add((run_id, number, position))
                    retrying.append(_side_name(bot, position))
                continue
            if state == "waiting" and (run_id, number, position) not in _WAITING:
                _WAITING.add((run_id, number, position))
                waiting.append(_side_name(bot, position))
            applied = applied or state in ("filled", "failed")
            if state == "failed":
                problems.append(f"{_side_name(bot, position)}: {'; '.join(notes)}")
        if problems:
            bot_exits._alert(
                deps,
                f"{bot.name}: time action {number} not fully applied",
                f"{action.label}. {'. '.join(problems)}. It won't be tried again. "
                "Check your broker.",
                bot.id,
                run_id=run_id,
            )
        if waiting:
            bot_exits._alert(
                deps,
                f"{bot.name}: time action {number} waiting for a price",
                f"{action.label}. There's no fresh price for the short on {', '.join(waiting)}, "
                "so its stop can't be checked and wasn't touched. The app tries again at each "
                "check.",
                bot.id,
                run_id=run_id,
            )
        if retrying:
            bot_exits._alert(
                deps,
                f"{bot.name}: time action {number} couldn't run",
                f"{action.label}. Something went wrong reading {', '.join(retrying)}; nothing "
                "was moved and the app keeps trying. Check your broker.",
                bot.id,
                run_id=run_id,
            )
        if applied and late:
            bot_exits._alert(
                deps,
                f"{bot.name}: time action {number} fired late",
                f"{action.label} fired at {local:%a %H:%M}: the app missed its time.",
                bot.id,
                run_id=run_id,
            )
