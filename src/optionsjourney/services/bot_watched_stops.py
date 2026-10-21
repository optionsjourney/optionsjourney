"""Watching the stops the app holds (bots slice 11): one held on its short while its group's
profit target rests (decision 0048; a Schwab stop opened before decision 0060 is held this
way too, migration 0036), and (slice 13) every group with no single short to rest a stop
on, on any broker.

Nothing rests at the broker for them: the app reads each watched stop every 2 s in the
regular session and, on two fresh passes at or past the stop, closes the group the way
a profit target does (Tracker2's engine-watched stop: watch_loop._armed_exit_pass,
_fire_armed, watch_rules.evaluate_armed). A stop held on its short reads its short's
mid; a watched group reads every leg and its whole P&L (watch_rules._usd_stop_outcome),
and one leg without a fresh, usable quote blocks the group. Because the stop only works
while the app does, every way it can stop working is said out loud: a stale price after a minute
(_track_stale), a gap in watching with its times (the computer slept, the app was off;
also at startup), a stop reached under Freeze, and a stop that has fired 3 times in a
day without closing its group. Never raises out of watch_pass or recover."""

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import exit_math, underlyings, watched_stops
from optionsjourney.bots.watch import SESSION_OPEN
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close, session_now
from optionsjourney.db.models import Bot, BotExit, BotLegGroup, BotRun, Setting
from optionsjourney.services import bot_exits

logger = logging.getLogger(__name__)

STOP_BY = bot_exits.WATCHED_STOP_BY  # "close for the stop": always ends at market
HEARTBEAT_KEY = "_watched_stops_at"  # the last watch, written down for startup

_lock = threading.Lock()
_streaks: dict[int, int] = {}  # stop id -> passes in a row at or past its stop
_stale_since: dict[int, datetime] = {}  # stop id -> when its price went stale
_stale_said: set[int] = set()
_freeze_said: set[int] = set()
_held_said: set[tuple[int, str]] = set()  # (stop id, why it was held), said once each
_last: datetime | None = None  # the last watch, in memory
_written: datetime | None = None  # when the last watch was last written down


def reset() -> None:
    """For tests, and what a restart does: nothing in memory."""
    global _last, _written
    with _lock:
        _streaks.clear()
        _stale_since.clear()
        _stale_said.clear()
        _freeze_said.clear()
        _held_said.clear()
        _last = _written = None


@dataclass(frozen=True)
class _Stop:
    id: int
    run_id: int
    position: int
    short: str  # the leg a single-short stop reads; "" for a watched group
    legs: tuple[exit_math.GroupLeg, ...]  # every leg of the group, per unit
    trigger: Decimal  # the short's price; for a watched group, its P&L level per unit
    entry: Decimal | None  # a watched group's entry per unit (None: a leg has no fill)
    watched: bool  # a group watched on its whole P&L (slice 13)
    group: str  # the group's name, for alerts
    filled_at: datetime | None
    created_at: datetime
    since: datetime  # its row's last change: watched from no earlier than this
    bot_id: int
    bot_name: str
    underlying: str  # the quote its stream health reads (underlyings.watch_symbol)

    @property
    def level(self) -> str:
        """The stop in words: the short's price, or the group's P&L."""
        return f"P&L {watched_stops.signed(self.trigger)}" if self.watched else str(self.trigger)


def _stops(deps, now: datetime, today_too: bool = False) -> list[_Stop]:
    """Every watched stop whose options haven't expired; with `today_too`, those that
    expired today as well (startup after the close: they went unwatched to the end)."""
    today = now.astimezone(MARKET_TZ).date()
    with deps.session_factory() as session:
        rows = session.execute(
            select(BotExit, BotRun, Bot)
            .join(BotRun, BotRun.id == BotExit.run_id)
            .join(Bot, Bot.id == BotRun.bot_id)
            .where(BotExit.kind == "stop", BotExit.state == bot_exits.WATCHING)
            .order_by(BotExit.id)
        ).all()
        names = {
            (g.bot_id, g.position): g.name
            for g in session.scalars(
                select(BotLegGroup).where(BotLegGroup.bot_id.in_({bot.id for _, _, bot in rows}))
            )
        }
        out = []
        for row, run, bot in rows:
            if bot_exits.stop_expired(row, now) and not (
                today_too and row.created_at.astimezone(MARKET_TZ).date() == today
            ):
                continue
            legs, watched = bot_exits.exit_legs(row), bool(row.watched)
            name = names.get((bot.id, row.group_position)) or f"Group {row.group_position + 1}"
            out.append(_Stop(
                row.id, run.id, row.group_position,
                "" if watched else (row.anchor_symbol or row.short_symbol),
                legs, row.trigger,
                bot_exits.group_entry_of(run, legs) if watched else None,
                watched, name, run.filled_at, row.created_at, row.updated_at,
                # the quote its stream health reads: SPX for XSP (slice 16)
                bot.id, bot.name, underlyings.watch_symbol(bot.underlying or "SPX"),
            ))  # fmt: skip
        return out


def _in_session(now: datetime) -> bool:
    return session_now("SPY", now.astimezone(MARKET_TZ)) == "regular"


def watch_pass(deps) -> None:
    """Every 2 s (the bot-watch loop): each watched stop read once; in the regular
    session only. Never raises."""
    try:
        now = deps.now()
        if not _in_session(now):
            _quiet()
            return
        stops = _stops(deps, now)
        if not stops:
            _quiet()
            return
        _check_lapse(deps, stops, now, now)
        _beat(deps, now)
        frozen = bot_exits._frozen(deps)
        for stop in stops:
            try:
                _watch_one(deps, stop, now, frozen)
            except Exception:  # noqa: BLE001 -- one stop never stops the others
                logger.exception("couldn't watch stop %s", stop.id)
        _forget({s.id for s in stops})
    except Exception:  # noqa: BLE001 -- the loop goes on
        logger.exception("the watched stops' pass failed")


def recover(deps) -> None:
    """At startup: if watched stops went unwatched today while the app was off or
    asleep (from the last watch it wrote down), say so with the times. The passes that
    follow act on any stop still past its level. Never raises."""
    try:
        now = deps.now()
        stops = _stops(deps, now, today_too=True)
        if not stops:
            return
        local = now.astimezone(MARKET_TZ)
        _check_missed_sessions(deps, stops, local.date())
        close = session_close(local.date())
        if close is None:
            return
        end = min(now, datetime.combine(local.date(), close, MARKET_TZ))
        _check_lapse(deps, stops, now, end)
    except Exception:  # noqa: BLE001 -- startup recovery never stops the app
        logger.exception("couldn't check the watched stops at startup")


MISSED_KEY = "_watched_stops_missed"  # {bot id: [days already said]}, the last 10 days


def _missed_said(deps) -> dict:
    with deps.session_factory() as session:
        row = session.get(Setting, MISSED_KEY)
        try:
            found = json.loads(row.value) if row is not None else {}
        except ValueError:
            found = {}
    return found if isinstance(found, dict) else {}


def _write_missed_said(deps, said: dict, today) -> None:
    oldest = (today - timedelta(days=10)).isoformat()
    kept = {k: [d for d in v if d >= oldest] for k, v in said.items()}
    with deps.session_factory() as session:
        row = session.get(Setting, MISSED_KEY) or Setting(key=MISSED_KEY)
        row.value, row.updated_at = json.dumps(kept), deps.now()
        session.add(row)
        session.commit()


def _check_missed_sessions(deps, stops: list[_Stop], today) -> None:
    """Slice 14: a trade held overnight whose watched stops went a whole earlier session
    unwatched (the app was off all day) is said once at startup, per bot."""
    last = _last_watch(deps)
    bots: dict[int, tuple[str, int, list[str], datetime]] = {}
    for stop in stops:
        name, run_id, groups, earliest = bots.setdefault(
            stop.bot_id, (stop.bot_name, stop.run_id, [], stop.since)
        )
        if stop.group not in groups:
            groups.append(stop.group)
        bots[stop.bot_id] = (name, run_id, groups, min(earliest, stop.since))
    said = _missed_said(deps)
    for bot_id, (name, run_id, groups, earliest) in bots.items():
        days = [
            d for d in watched_stops.missed_sessions(last, earliest, today)
            if d.isoformat() not in said.get(str(bot_id), [])
        ]  # fmt: skip
        if not days:
            continue  # each missed day is said once, however often the app restarts
        said.setdefault(str(bot_id), []).extend(d.isoformat() for d in days)
        _write_missed_said(deps, said, today)
        when = ", ".join(f"{day:%a %b} {day.day}" for day in days)  # no %-d: Windows refuses it
        names = " and ".join(filter(None, (", ".join(groups[:-1]), groups[-1])))
        bot_exits._alert(
            deps,
            f"{name}: stops weren't watched all session",
            f"The app wasn't watching on {when} (New York), so nothing protected {names} "
            "during that whole session. Watching again now. Check your broker.",
            bot_id,
            run_id=run_id,
        )


def _quiet() -> None:
    """Outside the session, or nothing watched: no streak or stale clock carries over."""
    with _lock:
        _streaks.clear()
        _stale_since.clear()
        _stale_said.clear()
        _freeze_said.clear()
        _held_said.clear()


def _forget(ids: set[int]) -> None:
    with _lock:
        for book in (_streaks, _stale_since):
            for key in [k for k in book if k not in ids]:
                book.pop(key, None)
        _stale_said.intersection_update(ids)
        _freeze_said.intersection_update(ids)
        for said in [k for k in _held_said if k[0] not in ids]:
            _held_said.discard(said)


def _last_watch(deps) -> datetime | None:
    if _last is not None:
        return _last
    with deps.session_factory() as session:
        row = session.get(Setting, HEARTBEAT_KEY)
        if row is None:
            return None
        try:
            return datetime.fromisoformat(row.value)
        except ValueError:
            return None


def _beat(deps, now: datetime) -> None:
    """The last watch: in memory every pass, written down at most every 15 s."""
    global _last, _written
    _last = now
    if _written is not None and now - _written < watched_stops.HEARTBEAT_EVERY:
        return
    try:
        with deps.session_factory() as session:
            row = session.get(Setting, HEARTBEAT_KEY) or Setting(key=HEARTBEAT_KEY)
            row.value, row.updated_at = now.isoformat(), now
            session.add(row)
            session.commit()
        _written = now
    except Exception:  # noqa: BLE001 -- the watch goes on; startup just knows less
        logger.exception("couldn't write down the last watch")


def _check_lapse(deps, stops: list[_Stop], now: datetime, end: datetime) -> None:
    """A gap in watching longer than a minute (watched_stops.lapse_since) is said once,
    per bot, with its times; the gap then counts as said (the last watch moves on)."""
    global _last
    local = now.astimezone(MARKET_TZ)
    opened = datetime.combine(local.date(), SESSION_OPEN, MARKET_TZ)
    # Each stop has needed watching since it last changed (placed, re-armed after a
    # close, turned back on): a time with nothing to watch is no gap.
    earliest = min(s.since for s in stops)
    since = watched_stops.lapse_since(_last_watch(deps), end, opened, earliest)
    if since is None:
        return
    _last = end
    bots: dict[int, tuple[str, int, list[str]]] = {}
    for stop in stops:  # only stops the app watches: a resting one is never named here
        _, _, groups = bots.setdefault(stop.bot_id, (stop.bot_name, stop.run_id, []))
        if stop.group not in groups:
            groups.append(stop.group)
    start = since.astimezone(MARKET_TZ).strftime("%H:%M")
    stop_at = end.astimezone(MARKET_TZ).strftime("%H:%M")
    for bot_id, (name, run_id, groups) in bots.items():
        names = " and ".join(filter(None, (", ".join(groups[:-1]), groups[-1])))
        bot_exits._alert(
            deps,
            f"{name}: stops weren't watched",
            f"The app didn't watch this bot's stops from {start} to {stop_at} New York (it "
            f"was off, asleep or busy), and nothing at your broker protected {names} then. "
            "Watching again now: a stop still past its level closes on the next two checks, "
            "but a price that went past a stop and came back in that time wasn't acted on. "
            "Check your broker.",
            bot_id,
            run_id=run_id,
        )


def _watch_one(deps, stop: _Stop, now: datetime, frozen: bool) -> None:
    reading = _read_group(deps, stop, now) if stop.watched else _read_short(deps, stop, now)
    _track_stale(deps, stop, reading, now)
    with _lock:
        if not reading.fired:
            _streaks.pop(stop.id, None)
            return
        _streaks[stop.id] = _streaks.get(stop.id, 0) + 1
        if _streaks[stop.id] < watched_stops.CONFIRM_PASSES:
            return
        _streaks.pop(stop.id, None)
    _fire(deps, stop, reading, frozen, now)


def _subscribe(deps, stop: _Stop) -> None:
    # Subscribing is local and idempotent (the hub only adds symbols it lacks).
    deps.hub.subscribe({*(leg.symbol for leg in stop.legs), stop.underlying})


def _read_group(deps, stop: _Stop, now: datetime) -> watched_stops.Reading:
    """A watched group (slice 13): every leg's quote, its whole P&L at the mids. A read
    that breaks is no price (stale): it can't fire, and the stale clock runs."""
    try:
        _subscribe(deps, stop)
        quotes = [(leg, deps.hub.latest(leg.symbol)) for leg in stop.legs]
        quiet_ok = bot_exits.quotes_live(deps, (q for _, q in quotes), stop.underlying, now)
    except Exception:  # noqa: BLE001 -- no price: it can't fire, and the stale clock runs
        logger.exception("couldn't read the prices for watched stop %s", stop.id)
        quotes, quiet_ok = [(leg, None) for leg in stop.legs], False
    return watched_stops.evaluate_group(
        stop.group, quotes, stop.entry, stop.trigger, now, stop.filled_at, quiet_ok=quiet_ok
    )


def _read_short(deps, stop: _Stop, now: datetime) -> watched_stops.Reading:
    """A stop held on its short (slice 11): the short's mid against its trigger."""
    try:
        _subscribe(deps, stop)
        quote = deps.hub.latest(stop.short)
        quiet_ok = quote is not None and bot_exits.stream_healthy(
            deps, quote.source, stop.underlying, now
        )
    except Exception:  # noqa: BLE001 -- no price: it can't fire, and the stale clock runs
        logger.exception("couldn't read the price for watched stop %s", stop.id)
        quote, quiet_ok = None, False
    return watched_stops.evaluate(
        stop.short.strip(),
        quote.bid if quote is not None else None,
        quote.ask if quote is not None else None,
        quote.at if quote is not None else None,
        now,
        stop.filled_at,
        stop.trigger,
        quiet_ok=quiet_ok,
    )


def _held_reason(rows) -> str | None:
    """Why a reached stop's close couldn't be claimed, worth saying: "target" (a profit
    target may still rest), "close" (an earlier close may still be live), "move" (a stop
    move cut off part-way), "placing" (the group's exits are still being placed), or None
    (closed, or a close already runs on it: nothing to say)."""
    if bot_exits.target_may_rest(rows):
        return "target"
    held = bot_exits.held_back(rows)
    if held is not None:
        return held
    return "placing" if any(r.state == "placing" for r in rows) else None


def _say_held(deps, stop: _Stop, reading) -> None:
    """Review M-6: a reached stop on a group held back (an earlier close may still be
    live, so nothing new is sent) is said, worded by why, once for each reason (final
    review: a later, different hold is said too), never silently skipped."""
    with deps.session_factory() as session:
        rows = list(
            session.scalars(
                select(BotExit).where(
                    BotExit.run_id == stop.run_id, BotExit.group_position == stop.position
                )
            )
        )
    reason = _held_reason(rows)
    if reason is None:
        return
    with _lock:
        said = (stop.id, reason) in _held_said
        _held_said.add((stop.id, reason))
    if said:
        return
    g = stop.group
    held = {
        "target": f"a profit target on {g} may still rest at your broker",
        "close": f"an earlier close on {g} may still be working",
        "move": f"a change to {g}'s stop was cut off part-way and may still be working",
        "placing": f"{g}'s exits are still being placed",
    }[reason]
    then = (
        "The app tries again while the stop stays reached; check your broker."
        if reason == "placing"
        else f"Check your broker, then close {g} or press re-check on the run page."
    )
    bot_exits._alert(
        deps,
        f"{stop.bot_name}: stop reached, group held back",
        f"{reading.detail}, but {held}, so the app sent nothing. {then}",
        stop.bot_id,
        run_id=stop.run_id,
    )


def _track_stale(deps, stop: _Stop, reading, now: datetime) -> None:
    """watch_loop._track_stale for one stop: a minute without a usable price alerts
    once, and once more when prices are back."""
    with _lock:
        if reading.skipped is None:
            _stale_since.pop(stop.id, None)
            back = stop.id in _stale_said
            _stale_said.discard(stop.id)
            say = None
        else:
            back = False
            since = _stale_since.setdefault(stop.id, now)
            say = now - since >= watched_stops.STALE_ALERT_AFTER and stop.id not in _stale_said
            if say:
                _stale_said.add(stop.id)
            seconds = int((now - since).total_seconds())
    if back:
        bot_exits._alert(
            deps,
            f"{stop.bot_name}: stop watched again",
            f"Prices for {stop.group} are back; its stop at {stop.level} is watched again.",
            stop.bot_id,
            run_id=stop.run_id,
        )
    elif say:
        bot_exits._alert(
            deps,
            f"{stop.bot_name}: stop not watched (stale price)",
            f"No usable price for {stop.group} for {seconds} seconds ({reading.detail}), so its "
            f"stop at {stop.level} isn't being watched: nothing at your broker protects "
            f"{stop.group} until prices come back. Check that the app is connected, and check "
            "your broker.",
            stop.bot_id,
            run_id=stop.run_id,
        )


def _closes_today(session, stop: _Stop, now: datetime) -> int:
    """How many closes this group's watched stop started today (New York)."""
    today = now.astimezone(MARKET_TZ).date()
    return sum(
        1
        for row in session.scalars(
            select(BotExit).where(
                BotExit.run_id == stop.run_id,
                BotExit.group_position == stop.position,
                BotExit.kind == "close",
            )
        )
        if bot_exits._by_of(row.log) == STOP_BY
        and row.created_at.astimezone(MARKET_TZ).date() == today
    )


def _fire(deps, stop: _Stop, reading, frozen: bool, now: datetime) -> None:
    """The stop is reached (watch_loop._fire_armed): close the group for what's open.
    Under Freeze nothing is sent (said once). A group whose stop already started 3
    closes today is left to you: the stop fails and it alerts. Those 3 fires a day are
    a watched stop's only limit: watching again is never a re-arm (decision 0037)."""
    if frozen:
        with _lock:
            said = stop.id in _freeze_said
            _freeze_said.add(stop.id)
        if not said:
            bot_exits._alert(
                deps,
                f"{stop.bot_name}: stop reached, Freeze is on",
                f"{reading.detail}, but Freeze is on, so nothing was sent and {stop.group} is "
                "still open. It closes once you turn Freeze off, if it's still past its stop. "
                "Check your broker.",
                stop.bot_id,
                run_id=stop.run_id,
            )
        return
    with _lock:
        _freeze_said.discard(stop.id)
    with bot_exits.EXITS_LOCK:
        with deps.session_factory() as session:
            row = session.get(BotExit, stop.id)
            if row is None or row.state != bot_exits.WATCHING:
                return
            if bot_exits.stop_off(session, stop.run_id, stop.position):
                return
            if _closes_today(session, stop, now) >= watched_stops.FIRES_PER_DAY:
                row.state = "failed"
                bot_exits._log(row, deps, event="reached again after 3 closes today; left to you")
                session.commit()
                left = True
            else:
                left = False
                open_size = bot_exits.group_units(session, session.get(BotRun, stop.run_id),
                                                stop.position)  # fmt: skip
        if left:
            bot_exits._alert(
                deps,
                f"{stop.bot_name}: stop left to you",
                f"The stop on {stop.group} was reached again after 3 closes today that didn't "
                f"close it, so the app stopped watching it: {stop.group} has no stop. Close it "
                "yourself, or set a new stop from the run page. Check your broker.",
                stop.bot_id,
                run_id=stop.run_id,
            )
            return
        if open_size <= 0:
            return
        close_id = bot_exits.fire_close(
            deps, stop.run_id, stop.position, open_size, open_size, by=STOP_BY,
            detail=reading.detail,
        )  # fmt: skip
    if close_id is None:
        _say_held(deps, stop, reading)  # closed or busy: tried again while it holds
        return
    bot_exits._alert(
        deps,
        f"{stop.bot_name}: stop reached, closing the group",
        f"{reading.detail}. Closing {stop.group} now."
        if stop.watched
        else f"{reading.detail} ({bot_exits.WATCHED}). Closing {stop.group} now.",
        stop.bot_id,
        run_id=stop.run_id,
    )
