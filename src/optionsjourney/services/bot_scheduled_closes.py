"""Scheduled closes (#330, decision 0133; Tracker2's services/bots/scheduled_close.py).

A close you ask for on a TastyTrade trade of Cboe index options when no session it can
trade in is open (16:00-20:15, 09:25-09:30, a weekend or a holiday) is scheduled, not
sent: nothing at the broker changes, its stop and target keep resting. At `start_at`, the
next session's open plus the opening pause (20:16 or 09:31 New York), the monitor's pass
starts it as if you had clicked then: bot_manage.close_part or close_trade, which check
everything again (Freeze, the window, the trade still open, what's held).

- One scheduled close per trade at a time; Cancel on the run page and the Manage panel.
- Restart-safe: it's a row (bot_scheduled_closes), never held in memory.
- Claimed with a conditional update before anything starts, so it fires once.
- A trade that closed first (a target or stop filled) is skipped, quietly.
- A claim a crash cut off (state "started", result STARTING) is alerted after 2 minutes,
  as "may or may not have been sent".
- Freeze (Stop all bots) when it comes due cancels it: failed, not tried again, and the
  alert says to schedule it again. One that comes due in another gap (the app was off)
  is "rescheduled": the start schedules a new row.
- One waiting row per trade: schedule()'s check, and a partial unique index for a race.
- It alerts when scheduled, when it starts, and loudly when it can't start.

Never the run's own timed close (bot_trade_settings): that stays as it is."""

import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from optionsjourney.bots import after_hours
from optionsjourney.db.models import Bot, BotRun, BotScheduledClose
from optionsjourney.services import bot_exits, orders

logger = logging.getLogger(__name__)

SCHEDULED, STARTED, CANCELLED, SKIPPED, FAILED, RESCHEDULED = (
    "scheduled", "started", "cancelled", "skipped", "failed", "rescheduled",
)  # fmt: skip
STARTING = "starting"  # a claimed row's result until its close has started
LOST_AFTER = timedelta(minutes=2)
HOLDING = ("filled", "partial")


def _bot_name(session, run: BotRun) -> str:
    bot = session.get(Bot, run.bot_id)
    return bot.name if bot is not None else "A bot"


def latest(session_factory, run_id: int) -> BotScheduledClose | None:
    """The run's scheduled close still waiting, or None (the run page shows it)."""
    with session_factory() as session:
        row = session.scalar(
            select(BotScheduledClose)
            .where(BotScheduledClose.run_id == run_id, BotScheduledClose.state == SCHEDULED)
            .order_by(BotScheduledClose.id.desc())
            .limit(1)
        )
        if row is not None:
            session.expunge(row)
        return row


def words(row: BotScheduledClose, now) -> str:
    """What it will close, and when ("Close entire trade at 20:16 ET")."""
    what = "Close entire trade" if row.group_position is None else (
        f"Close {row.quantity or 'all'} of group {row.group_position + 1}"
    )  # fmt: skip
    how = " at market" if row.order_type == "market" else ""
    return f"{what}{how} at {after_hours.when_words(row.start_at, now)}"


def schedule(deps, run_id: int, *, start_at, position: int | None = None,
             groups=None, quantity: str | None = None,
             order_type: str = "limit") -> tuple[bool, str]:  # fmt: skip
    """Write the scheduled close and alert. (True, what you're told), or (False, why not):
    a trade already has one scheduled."""
    now = deps.now()
    when = after_hours.when_words(start_at, now)
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        existing = _waiting(session, run_id)
        if existing is not None:
            return False, _already(after_hours.when_words(existing.start_at, now))
        session.add(BotScheduledClose(
            run_id=run_id, group_position=position,
            groups=sorted(groups) if groups is not None else None, quantity=quantity,
            order_type=order_type, start_at=start_at, state=SCHEDULED,
            created_at=now, updated_at=now,
        ))  # fmt: skip
        try:
            session.commit()
        except IntegrityError:  # two clicks raced past the check: the index keeps one
            session.rollback()
            return False, _already(None)
        bot_id, name = run.bot_id, _bot_name(session, run)
    said = (
        f"The market is closed for these options now, so the close is scheduled for {when}, "
        "when they trade again. Nothing is sent before then: the stop and target stay as "
        "they are. Cancel it from the trade's Manage box."
    )
    bot_exits._alert(deps, f"{name}: close scheduled for {when}", said, bot_id,
                     run_id=run_id, routine=True)  # fmt: skip
    return True, said


def _waiting(session, run_id: int) -> BotScheduledClose | None:
    return session.scalar(
        select(BotScheduledClose).where(
            BotScheduledClose.run_id == run_id, BotScheduledClose.state == SCHEDULED
        )
    )


def _already(when: str | None) -> str:
    at = f" at {when}" if when else ""
    return (
        f"A close is already scheduled for this trade{at}. Cancel it first to schedule "
        "another. Nothing was changed."
    )


def cancel(deps, run_id: int, row_id: int) -> tuple[bool, str]:
    """Cancel a scheduled close that hasn't started. (False, why) once it has."""
    now = deps.now()
    with deps.session_factory() as session:
        done = session.execute(
            update(BotScheduledClose)
            .where(BotScheduledClose.id == row_id, BotScheduledClose.run_id == run_id,
                   BotScheduledClose.state == SCHEDULED)
            .values(state=CANCELLED, result="cancelled by you", updated_at=now)
        ).rowcount  # fmt: skip
        session.commit()
        if done == 1:
            return True, "The scheduled close is cancelled. Nothing was sent."
        row = session.get(BotScheduledClose, row_id)
    if row is None or row.run_id != run_id:
        return False, "That scheduled close isn't there."
    if row.state == STARTED:
        return False, (
            "That close has already started. Use Cancel on the running close, or check the "
            "run's log."
        )
    return False, f"That scheduled close is {row.state} already."


def _claim(session_factory, row_id: int, state: str, result: str, now) -> bool:
    with session_factory() as session:
        done = session.execute(
            update(BotScheduledClose)
            .where(BotScheduledClose.id == row_id, BotScheduledClose.state == SCHEDULED)
            .values(state=state, result=result, updated_at=now)
        ).rowcount
        session.commit()
        return done == 1


def _note(session_factory, row_id: int, state: str, result: str, now) -> None:
    with session_factory() as session:
        row = session.get(BotScheduledClose, row_id)
        row.state, row.result, row.updated_at = state, result, now
        session.commit()


def _open(session, run: BotRun | None) -> bool:
    """Whether anything of the trade is still open by the app's records."""
    if run is None or run.status not in HOLDING or not run.filled_legs:
        return False
    from optionsjourney.services import bot_manage

    return any(
        (side := bot_manage._read(session, run.id, position)) is not None and side.state != "closed"
        for position in sorted({leg["group"] for leg in run.legs})
    )


def _not_started(deps, run_id: int, bot_id: int | None, name: str, why: str) -> None:
    bot_exits._alert(
        deps, f"{name}: scheduled close NOT started",
        f"The close scheduled for this trade didn't start: {why} The trade is still open: "
        "close it yourself, or check your broker.", bot_id, run_id=run_id,
    )  # fmt: skip


def _frozen(deps) -> bool:
    try:
        return orders.switches(deps.session_factory).freeze
    except Exception:  # noqa: BLE001 -- the plain wording then
        return False


def _rescheduled(deps, run_id: int, row_id: int) -> str | None:
    """When the start made a newer scheduled close for the run: its time in words."""
    with deps.session_factory() as session:
        newer = session.scalar(
            select(BotScheduledClose).where(
                BotScheduledClose.run_id == run_id, BotScheduledClose.state == SCHEDULED,
                BotScheduledClose.id > row_id,
            )
        )  # fmt: skip
        return None if newer is None else after_hours.when_words(newer.start_at, deps.now())


def _sweep_lost(deps) -> None:
    """A claim the app stopped between its claim and its start (still STARTING after
    LOST_AFTER): alerted once, and marked failed."""
    now = deps.now()
    with deps.session_factory() as session:
        lost = [
            (r.id, r.run_id)
            for r in session.scalars(select(BotScheduledClose).where(
                BotScheduledClose.state == STARTED, BotScheduledClose.result == STARTING))
            if now - r.updated_at >= LOST_AFTER
        ]  # fmt: skip
    for row_id, run_id in lost:
        _note(deps.session_factory, row_id, FAILED, "the app stopped while starting it", now)
        with deps.session_factory() as session:
            run = session.get(BotRun, run_id)
            bot_id = run.bot_id if run is not None else None
            name = _bot_name(session, run) if run is not None else "A bot"
        bot_exits._alert(
            deps, f"{name}: scheduled close may not have started",
            "The app stopped while it was starting the close scheduled for this trade, so "
            "it may or may not have been sent: check your broker before closing it yourself.",
            bot_id, run_id=run_id,
        )  # fmt: skip


def fire_due(deps) -> int:
    """Start every scheduled close whose time has come (the monitor's pass, every 30 s,
    and at startup). Returns how many started. One row's trouble never stops the rest."""
    from optionsjourney.services import bot_manage

    try:
        _sweep_lost(deps)
    except Exception:  # noqa: BLE001 -- the due closes still start
        logger.exception("the scheduled closes' sweep failed")
    now = deps.now()
    with deps.session_factory() as session:
        due = [
            (r.id, r.run_id, r.group_position, r.groups, r.quantity, r.order_type)
            for r in session.scalars(
                select(BotScheduledClose)
                .where(BotScheduledClose.state == SCHEDULED, BotScheduledClose.start_at <= now)
                .order_by(BotScheduledClose.id)
            )
        ]
    started = 0
    for row_id, run_id, position, groups, quantity, order_type in due:
        claimed, bot_id, name = False, None, "A bot"
        try:
            with deps.session_factory() as session:
                run = session.get(BotRun, run_id)
                is_open = _open(session, run)
                if run is not None:
                    bot_id, name = run.bot_id, _bot_name(session, run)
            if not is_open:
                _claim(deps.session_factory, row_id, SKIPPED, "the trade was already closed",
                       now)  # fmt: skip
                continue
            if not _claim(deps.session_factory, row_id, STARTED, STARTING, now):
                continue  # cancelled meanwhile, or another pass took it
            claimed = True
            if position is None:
                outcome = bot_manage.close_trade(
                    deps, run_id, order_type, set(groups) if groups is not None else None
                )
            else:
                outcome = bot_manage.close_part(deps, run_id, position, quantity or "",
                                                order_type)  # fmt: skip
            again = _rescheduled(deps, run_id, row_id) if outcome.ok else None
            if again is not None:
                # It came due in another gap (the app was off, say): close_trade/close_part
                # scheduled a new one, so this one is moved, not started
                said = f"rescheduled for {again}"
                _note(deps.session_factory, row_id, RESCHEDULED, said, deps.now())
                bot_exits._alert(deps, f"{name}: scheduled close {said}", outcome.message,
                                 bot_id, run_id=run_id, routine=True)  # fmt: skip
                continue
            _note(deps.session_factory, row_id, STARTED if outcome.ok else FAILED,
                  outcome.message, deps.now())  # fmt: skip
            if not outcome.ok and _frozen(deps):
                _not_started(deps, run_id, bot_id, name, (
                    f"{outcome.message} Stop all bots (Freeze) cancels a scheduled close: it "
                    "isn't tried again. Once Freeze is off, schedule it again or close it."
                ))  # fmt: skip
            elif outcome.ok:
                started += 1
                bot_exits._alert(deps, f"{name}: scheduled close started", outcome.message,
                                 bot_id, run_id=run_id, routine=True)  # fmt: skip
            else:
                _not_started(deps, run_id, bot_id, name, outcome.message)
        except Exception as e:  # noqa: BLE001 -- one row never strands the rest
            logger.exception("scheduled close %s failed to start", row_id)
            if claimed:
                try:
                    _note(deps.session_factory, row_id, FAILED, f"error: {e.__class__.__name__}",
                          deps.now())  # fmt: skip
                except Exception:  # noqa: BLE001 -- the alert still goes out
                    logger.exception("couldn't mark scheduled close %s failed", row_id)
                _not_started(deps, run_id, bot_id, name,
                             f"something went wrong ({e.__class__.__name__}).")  # fmt: skip
    return started
