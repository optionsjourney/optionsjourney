"""Which trading session a symbol is in (#26), for pricing: is a quote expected
to move, and when it next will. Ported from Tracker2's services/sessions.py,
with fixed CME hours for futures (Tracker2 asks TastyTrade for them).

- Stocks: 09:30 to the NYSE close.
- SPX, XSP, VIX, RUT and their weeklies: the regular session, Curb 16:15-17:00
  (not on half days), and Global Trading Hours from 20:15 the evening before
  a trading day to 09:25 on it.
- Futures (/ES, ...): CME Globex, Sunday 18:00 to Friday 17:00, halted
  17:00-18:00 each day.

The holiday table below is the fallback: NYSE's published calendar, once the
app has fetched it (services/economic_events.py), replaces it for every year it
covers (use_fetched_calendar).

Times are New York wall clock."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from optionsjourney.core.market import MARKET_TZ

NYSE_HOLIDAYS = frozenset(
    date.fromisoformat(d)
    for d in (
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
        "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    )
)  # fmt: skip
NYSE_EARLY_CLOSES = frozenset(
    date.fromisoformat(d) for d in ("2026-11-27", "2026-12-24", "2027-11-26")
)
CBOE_INDEX_ROOTS = frozenset({"SPX", "SPXW", "XSP", "VIX", "VIXW", "RUT", "RUTW"})
REGULAR_OPEN, FULL_DAY_CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)
CURB_OPEN, CURB_CLOSE = time(16, 15), time(17, 0)
GTH_OPEN, GTH_CLOSE = time(20, 15), time(9, 25)
CME_OPEN, CME_CLOSE = time(18, 0), time(17, 0)
_ROOT = re.compile(r"^\.?(/)?([A-Z]+)")


def naive_et(moment: datetime) -> datetime:
    return moment if moment.tzinfo is None else moment.astimezone(MARKET_TZ).replace(tzinfo=None)


@dataclass(frozen=True)
class FetchedCalendar:
    """NYSE's published calendar as the app last stored it (bots slice 5): its
    closures, its early closes, and the years it covers."""

    closed: frozenset[date] = frozenset()
    early: Mapping[date, time] = field(default_factory=dict)
    years: frozenset[int] = frozenset()


_fetched = FetchedCalendar()


def use_fetched_calendar(closed: Iterable[date], early: Mapping[date, time]) -> None:
    """Use NYSE's fetched calendar for every year it has closures in; the table
    above stays the fallback for every other year, so a broken fetch never lets
    a bot trade a known holiday (Tracker2's guards._build_calendar). The services
    hand it the stored rows at startup and after each refresh; it's swapped in
    whole, so a reader never sees half of one."""
    global _fetched  # noqa: PLW0603 -- one calendar for the whole app
    closed = frozenset(closed)
    _fetched = FetchedCalendar(closed, dict(early), frozenset(d.year for d in closed))


def session_close(day: date) -> time | None:
    """The regular session's close: 13:00 on a half day, None when shut."""
    if day.weekday() >= 5:
        return None
    fetched = _fetched
    if day.year in fetched.years:
        return None if day in fetched.closed else fetched.early.get(day, FULL_DAY_CLOSE)
    if day in NYSE_HOLIDAYS:
        return None
    return EARLY_CLOSE if day in NYSE_EARLY_CLOSES else FULL_DAY_CLOSE


def next_session_after(day: date) -> date:
    """The next day after `day` with a regular session."""
    nxt = day + timedelta(days=1)
    while session_close(nxt) is None:
        nxt += timedelta(days=1)
    return nxt


def calendar_covers(day: date) -> bool:
    """Whether the calendar (fetched or the table) covers `day`'s year. When it
    doesn't, session_close can't tell a holiday from a trading day."""
    return day.year in _fetched.years or any(h.year == day.year for h in NYSE_HOLIDAYS)


def calendar_through() -> int:
    """The last year the calendar covers."""
    return max({h.year for h in NYSE_HOLIDAYS} | _fetched.years)


def group_of(symbol: str) -> str:
    found = _ROOT.match((symbol or "").strip().upper())
    if not found:
        return "equity"
    if found.group(1):
        return "futures"
    return "cboe_index" if found.group(2) in CBOE_INDEX_ROOTS else "equity"


def _cme_open(now: datetime) -> bool:
    weekday, t = now.weekday(), now.time()
    if weekday == 5:
        return False
    if weekday == 6:
        return t >= CME_OPEN
    if weekday == 4:
        return t < CME_CLOSE
    return t < CME_CLOSE or t >= CME_OPEN


def session_now(symbol: str, now: datetime) -> str | None:
    """ "regular", "curb", "overnight", "futures", or None when it isn't trading."""
    now = naive_et(now)
    group, t, day = group_of(symbol), now.time(), now.date()
    if group == "futures":
        return "futures" if _cme_open(now) else None
    close = session_close(day)
    if group == "equity":
        return "regular" if close is not None and REGULAR_OPEN <= t < close else None
    if close is not None:
        if REGULAR_OPEN <= t < close:
            return "regular"
        if close >= FULL_DAY_CLOSE and CURB_OPEN <= t < CURB_CLOSE:
            return "curb"
        if t < GTH_CLOSE:
            return "overnight"  # began 20:15 the evening before
    if t >= GTH_OPEN and session_close(day + timedelta(days=1)) is not None:
        return "overnight"
    return None


def next_open(symbol: str, now: datetime) -> datetime | None:
    """The first five-minute mark within 8 days when `symbol` trades; None if
    it trades now. Every session boundary here is on a five-minute mark."""
    now = naive_et(now)
    if session_now(symbol, now) is not None:
        return None
    t = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
    end = now + timedelta(days=8)
    while t <= end:
        if t >= now and session_now(symbol, t) is not None:
            return t
        t += timedelta(minutes=5)
    return None
