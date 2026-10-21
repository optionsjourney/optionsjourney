"""When a timed close and a time action are due (Tracker2's timed_close.py and
time_actions.py). Pure: the passes hand in New York wall-clock time (naive) and
the calendar comes from core.sessions, which knows slice 5's half days."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from optionsjourney.bots.definition import TimeActionDef
from optionsjourney.core.sessions import session_close
from optionsjourney.core.symbols import parse_occ

# On a half day a set time at or after the close can't be kept: fire this long
# before the close instead, so the chase has time (timed_close.EARLY_CLOSE_LEAD).
EARLY_CLOSE_LEAD = timedelta(minutes=15)
SESSION_OPEN = time(9, 30)


def hhmm(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def effective_time(configured: time, close: time) -> time:
    """The set time, pulled in to 15 minutes before the session's close when the
    close comes first (timed_close._effective_close_time)."""
    if configured < close:
        return configured
    latest = (datetime.combine(date.min, close) - EARLY_CLOSE_LEAD).time()
    return min(configured, latest)


def nearest_expiration(symbols: Iterable[str]) -> date | None:
    """The trade's nearest expiration, from its OCC symbols (dte_for_position)."""
    found = [parsed[1].expiration for s in symbols if (parsed := parse_occ(s)) is not None]
    return min(found) if found else None


def timed_close_due(close_time: str, close_dte: int, expiration: date | None,
                    now_et: datetime) -> str | None:  # fmt: skip
    """fire_timed_closes' due rule for today: "due" once the (half-day adjusted) time
    has come in the session, "over" once the session has ended (a close that never
    ran is missed), None when it isn't due today: not a session day, the trade more
    than `close_dte` calendar days from expiring, or the time still to come."""
    today = now_et.date()
    close = session_close(today)
    if close is None or expiration is None or (expiration - today).days > close_dte:
        return None
    if now_et.time() >= close:
        return "over"
    if now_et.time() >= effective_time(hhmm(close_time), close):
        return "due"
    return None


@dataclass(frozen=True)
class NextClose:
    """When a timed close next fires for one group (timed_close.next_close's dict)."""

    day: date
    at: str  # HH:MM New York, pulled in on a half day
    due_now: bool  # today, its time passed with the session open: the next check closes it
    nearest: date  # the group's nearest expiration
    dte: int  # days from `day` to it


def next_close(close_time: str | None, close_dte: int, nearest: date | None, now_et: datetime,
               fired_today: bool) -> NextClose | None:  # fmt: skip
    """The same due rule as fire_timed_closes, for the run page (Tracker2's
    timed_close.next_close): the first session day, from the later of today and the
    nearest expiration less `close_dte` days, that hasn't closed and (today) hasn't had
    this group's timed close, at the time pulled in on a half day. None when it's off,
    nothing is held, or no session is left before the nearest expiration."""
    if not close_time or nearest is None:
        return None
    try:
        configured = hhmm(close_time)
    except ValueError:
        return None
    today = now_et.date()
    day = max(today, nearest - timedelta(days=close_dte or 0))
    while day <= nearest:
        close = session_close(day)
        if close is None or (day == today and (now_et.time() >= close or fired_today)):
            day += timedelta(days=1)
            continue
        at = effective_time(configured, close)
        return NextClose(day, at.strftime("%H:%M"), day == today and now_et.time() >= at,
                         nearest, (nearest - day).days)  # fmt: skip
    return None


def previous_session(day: date) -> date:
    """`day` when it's a session, else the last session before it (bounded)."""
    for _ in range(10):
        if session_close(day) is not None:
            return day
        day -= timedelta(days=1)
    return day


def nth_session(start: date, n: int) -> date:
    """The n-th session after `start` (0 = `start` itself)."""
    day = start
    for _ in range(int(n)):
        day += timedelta(days=1)
        for _ in range(10):
            if session_close(day) is not None:
                break
            day += timedelta(days=1)
    return day


def action_due_day(action: TimeActionDef, expiration: date | None, fill_day: date) -> date | None:
    """time_actions.due_day: the day a row first becomes due, never before the fill.
    DTE: `days` calendar days before the nearest expiration, stepped back to a
    session ("1 DTE" on a Monday expiry is the Friday before). DIT: the `days`-th
    session after the fill day."""
    if action.type == "dte":
        if expiration is None:
            return None
        return max(previous_session(expiration - timedelta(days=action.days)), fill_day)
    return nth_session(fill_day, action.days)


def action_due(action: TimeActionDef, expiration: date | None, fill_day: date,
               now_et: datetime) -> tuple[bool, bool]:  # fmt: skip
    """(due, late), time_actions.evaluate: due on its day once its (half-day adjusted)
    time has come; on any later day from the open, and late. Never outside the
    regular session."""
    today = now_et.date()
    day = action_due_day(action, expiration, fill_day)
    if day is None or today < day:
        return False, False
    late = today > day
    close = session_close(today)
    if close is None or now_et.time() >= close or now_et.time() < SESSION_OPEN:
        return False, late
    if not late and now_et.time() < effective_time(hhmm(action.at), close):
        return False, False
    return True, late


# A "close" is one you asked for (slice 6): "filled" closed everything still open,
# "expired" found nothing held; "reduced" closed part, and the side stays open.
CLOSED = (("target", "filled"), ("timed", "filled"), ("timed", "expired"),
          ("close", "filled"), ("close", "expired"),
          ("stop", "filled"), ("stop", "expired"))  # fmt: skip
BUSY = ("placing", "firing", "chasing")


def side_state(exits: Iterable[tuple[str, str]]) -> str:
    """A side, from its exit rows' (kind, state): "closed" (its target, a timed
    close or a close you asked for closed it, its stop filled, or its position is
    gone), "busy" (a close is running on it, or its exits are still being placed:
    wait), else "open"."""
    rows = list(exits)
    if any(row in CLOSED for row in rows):
        return "closed"
    if any(state in BUSY for _, state in rows):
        return "busy"
    return "open"
