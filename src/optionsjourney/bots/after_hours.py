"""Which window a close you ask for falls in, and whether a stop can be sent now, for
a trade on Cboe index options (#330, decision 0133; Tracker2's
services/bots/after_hours.py and its follow-up fixes). Pure: no Flask, database or
broker.

SPX, SPXW, XSP, VIX, VIXW, RUT and RUTW options also trade in Cboe's Global Trading
Hours (GTH): from 20:15 New York time on the evening before a trading day to 09:25 on
it (core/sessions.py). TastyTrade takes LIMIT orders then, with the "GTC Ext Overnight"
time in force (TimeInForce.GTC_OVERNIGHT), but refuses every STOP outside the regular
session. A trade is after-hours capable when it is at TastyTrade and every leg is a Cboe
index option; everything else (Schwab, stock and ETF options, futures) keeps the
regular-session rules and their message.

The windows of a capable trade's close:
  REGULAR     09:30 to the close: as before.
  POST_CLOSE  16:00-16:15 on a full day, behind POST_CLOSE_ENABLED (off: that quarter
              hour is part of the scheduled gap). Options expiring today stopped at 16:00.
  OVERNIGHT   inside GTH: a chase of GTC Ext Overnight limits, never at market.
  SCHEDULE    a close that can't work yet (16:00-20:15, 09:25-09:30, weekends, a holiday):
              it starts at `start_at`, the next session's open plus the opening pause.
  CLOSED      refused, with `message` (every leg has expired, or the calendar isn't known).

An overnight session belongs to the next trading day (trading_day). Times are New York
wall clock; an aware datetime is read in New York, a naive one is taken as New York."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from optionsjourney.core import sessions
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.symbols import parse_occ

# !!! BEFORE SETTING THIS TO True (Tracker2's after_hours.POST_CLOSE_ENABLED, its ruling R11):
#  1. A close you ask for 16:00-16:15 would be a Day chase while a resting closing order of
#     the same legs may still be live (a paired close's DAY sale, bot_exits._paired_close,
#     or a wing sale): make sure that close settles those first, as the regular-hours
#     close does, so nothing is sold twice.
#  2. Check on a full trading day at about 16:05 that TastyTrade accepts, and works (not
#     just queues), a 16:00-16:15 limit on an index option. Until both are done 16:00-16:15
#     is part of the scheduled gap for closes.
POST_CLOSE_ENABLED = False

REGULAR = "regular"
POST_CLOSE = "post_close"
OVERNIGHT = "overnight"
SCHEDULE = "schedule"
CLOSED = "closed"

TASTYTRADE = "tastytrade"
OPENING_PAUSE = timedelta(seconds=60)  # bots/watch.OPENING_PAUSE: 09:31:00 and 20:16:00
POST_CLOSE_END = time(16, 15)
STOPS_AT = time(9, 30, 5)  # Tracker2's place_pre_open_protections job
STOPS_RETRY_AT = time(9, 45)  # and its 09:45 retry
_INDEX = "SPX"  # any Cboe index root: they share one calendar

MARKET_CLOSED = "You can close only while the market is open (its regular session)."
EXPIRED = "These options have expired, so there's nothing left to trade."
EXPIRING_TODAY = "Options expiring today stopped trading at 16:00."
NO_CALENDAR = (
    "The market calendar for {year} isn't known yet, so nothing is sent outside the "
    "regular session."
)
MARKET_OVERNIGHT = (
    "Market orders aren't taken outside the regular session. Use Limit: it closes "
    "with overnight limit orders, starting from the mid."
)
STOP_EDIT_OFF_HOURS = (
    "Stops can't be changed until 09:30 ET: the broker takes no stop orders outside the "
    "regular session. Your stop stays as it is."
)
STOP_ON_OFF_HOURS = (
    "A stop can't be switched on until 09:30 ET: the broker takes no stop orders outside "
    "the regular session. It stays off for now."
)


@dataclass(frozen=True)
class Window:
    kind: str
    start_at: datetime | None = None  # SCHEDULE: when it starts (New York, aware)
    message: str | None = None  # CLOSED: why

    @property
    def overnight(self) -> bool:
        return self.kind == OVERNIGHT

    @property
    def can_close_now(self) -> bool:
        return self.kind in (REGULAR, POST_CLOSE, OVERNIGHT)


def _et(now: datetime) -> datetime:
    return sessions.naive_et(now)


def capable(broker: str | None, symbols) -> bool:
    """TastyTrade, and every leg a Cboe index option."""
    symbols = [s for s in (symbols or ()) if s]
    return (
        (broker or "").lower() == TASTYTRADE
        and bool(symbols)
        and all(sessions.group_of(s) == "cboe_index" for s in symbols)
    )


def expiry_of(symbol: str) -> date | None:
    parsed = parse_occ(symbol or "")
    return parsed[1].expiration if parsed is not None else None


def _all_expired_before(symbols, day: date) -> bool:
    """Every leg expired before trading day `day`: nothing is left to trade (Tracker2's
    7d1e2f67: a calendar whose front leg has expired still has its back leg)."""
    expiries = [expiry_of(s) for s in symbols]
    return bool(expiries) and all(e is not None and e < day for e in expiries)


def trading_day(now: datetime) -> date:
    """The trading day `now` belongs to: from 20:15 an overnight session belongs to the
    next trading day; before that, today."""
    now = _et(now)
    if now.time() < sessions.GTH_OPEN:
        return now.date()
    return sessions.next_session_after(now.date())


def starts_overnight(start: datetime) -> bool:
    """Whether a scheduled start is a GTH open (20:16), not a 09:31."""
    return _et(start).time() >= sessions.GTH_OPEN


def next_start(now: datetime) -> datetime:
    """The next moment a scheduled close can work (aware, New York): the sooner of
    today's 09:30 and the next GTH open (20:15 on an evening before a trading day), each
    plus the opening pause, so it never prices off the first minute's wide quotes."""
    now = _et(now)
    found = []
    if sessions.session_close(now.date()) is not None:
        first = datetime.combine(now.date(), sessions.REGULAR_OPEN) + OPENING_PAUSE
        if first > now:
            found.append(first)
    day = now.date()
    for _ in range(10):
        opens = datetime.combine(day, sessions.GTH_OPEN) + OPENING_PAUSE
        if sessions.session_close(day + timedelta(days=1)) is not None and opens > now:
            found.append(opens)
            break
        day += timedelta(days=1)
    return min(found).replace(tzinfo=MARKET_TZ)


def _post_close(now: datetime) -> bool:
    return (
        POST_CLOSE_ENABLED
        and sessions.session_close(now.date()) == sessions.FULL_DAY_CLOSE
        and sessions.FULL_DAY_CLOSE <= now.time() < POST_CLOSE_END
    )


def _known(day: date) -> bool:
    return sessions.calendar_covers(day) and sessions.calendar_covers(day + timedelta(days=10))


def close_window(broker: str | None, symbols, now: datetime) -> Window:
    """The window a close you ask for falls in now (Tracker2's after_hours.close_window)."""
    now = _et(now)
    symbols = [s for s in (symbols or ()) if s]
    if not capable(broker, symbols):
        if sessions.session_now("SPY", now) == "regular":
            return Window(REGULAR)
        return Window(CLOSED, message=MARKET_CLOSED)
    session = sessions.session_now(_INDEX, now)
    if session == "regular":
        return Window(REGULAR)
    if not _known(now.date()):
        return Window(CLOSED, message=NO_CALENDAR.format(year=now.year))
    if _post_close(now):
        if any(expiry_of(s) == now.date() for s in symbols):
            return Window(CLOSED, message=EXPIRING_TODAY)
        return Window(POST_CLOSE)
    if session == "overnight":
        if _all_expired_before(symbols, trading_day(now)):
            return Window(CLOSED, message=EXPIRED)
        return Window(OVERNIGHT)
    start = next_start(now)
    if _all_expired_before(symbols, trading_day(start)):
        # e.g. 17:10 on expiry day: the next session belongs to a later day
        return Window(CLOSED, message=EXPIRED)
    return Window(SCHEDULE, start_at=start)


def stops_refused(broker: str | None, symbols, now: datetime) -> bool:
    """Whether a stop for this trade must wait for the regular session: a capable trade
    outside it (TastyTrade refuses every stop then, "tif_session_denied")."""
    return capable(broker, symbols) and sessions.session_now(_INDEX, _et(now)) != "regular"


def stops_due(now: datetime) -> bool:
    """Whether stops held for the open may go now: the regular session, from 09:30:05."""
    now = _et(now)
    return sessions.session_now(_INDEX, now) == "regular" and now.time() >= STOPS_AT


def retry_due(now: datetime) -> bool:
    """Whether a held stop the open refused may be tried again: from 09:45."""
    return stops_due(now) and _et(now).time() >= STOPS_RETRY_AT


def when_words(start: datetime, now: datetime) -> str:
    """ "20:16 ET", or with its day when it isn't today ("Sun 20:16 ET")."""
    start, now = _et(start), _et(now)
    hhmm = f"{start:%H:%M} ET"
    return hhmm if start.date() == now.date() else f"{start:%a} {hhmm}"
