"""after_hours (#330, decision 0133): which window a close you ask for falls in, and when
a stop can be sent, for a trade on Cboe index options (Tracker2's test_bots_after_hours)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from optionsjourney.bots import after_hours as ah
from optionsjourney.core import sessions
from optionsjourney.core.market import MARKET_TZ

TT = "tastytrade"
SPXW = ["SPXW  261218C07000000"]
EXPIRING = ["SPXW  261104C07750000"]  # expires Wed 2026-11-04
SPY = ["SPY   261106C00600000"]
CALENDAR = ["SPXW  261105C07750000", "SPXW  261106C07750000"]


@pytest.fixture(autouse=True)
def table_calendar():
    sessions.use_fetched_calendar([], {})  # the built-in NYSE table
    yield
    sessions.use_fetched_calendar([], {})


def dt(day, hh, mm, ss=0, month=11):
    return datetime(2026, month, day, hh, mm, ss)


def et(day, hh, mm, ss=0):
    return dt(day, hh, mm, ss).replace(tzinfo=MARKET_TZ)


@pytest.mark.parametrize(
    "now,symbols,broker,flag,kind,start",
    [
        (dt(4, 10, 0), SPXW, TT, False, ah.REGULAR, None),
        (dt(4, 16, 5), SPXW, TT, True, ah.POST_CLOSE, None),
        (dt(4, 16, 5), SPXW, TT, False, ah.SCHEDULE, et(4, 20, 16)),  # the gate is off
        (dt(4, 16, 5), EXPIRING, TT, True, ah.CLOSED, None),
        (dt(4, 17, 10), SPXW, TT, False, ah.SCHEDULE, et(4, 20, 16)),
        (dt(4, 20, 15), SPXW, TT, False, ah.OVERNIGHT, None),
        (dt(5, 3, 0), SPXW, TT, False, ah.OVERNIGHT, None),
        (dt(5, 9, 24, 59), SPXW, TT, False, ah.OVERNIGHT, None),
        (dt(5, 9, 25), SPXW, TT, False, ah.SCHEDULE, et(5, 9, 31)),
        (dt(5, 9, 26), SPXW, TT, False, ah.SCHEDULE, et(5, 9, 31)),
        (dt(5, 9, 30), SPXW, TT, False, ah.REGULAR, None),
        (dt(6, 20, 30), SPXW, TT, False, ah.SCHEDULE, et(8, 20, 16)),  # Friday night
        (dt(7, 12, 0), SPXW, TT, False, ah.SCHEDULE, et(8, 20, 16)),  # Saturday
        (dt(8, 20, 15), SPXW, TT, False, ah.OVERNIGHT, None),  # Sunday 20:15
        (dt(25, 20, 30), SPXW, TT, False, ah.SCHEDULE, et(26, 20, 16)),  # Thanksgiving eve
        (dt(4, 17, 10), SPY, TT, False, ah.CLOSED, None),
        (dt(4, 20, 30), SPXW, "schwab", False, ah.CLOSED, None),
        (dt(4, 10, 0), SPXW, "schwab", False, ah.REGULAR, None),
        (dt(4, 10, 0), SPY, TT, False, ah.REGULAR, None),
    ],
)
def test_close_window_table(monkeypatch, now, symbols, broker, flag, kind, start):
    monkeypatch.setattr(ah, "POST_CLOSE_ENABLED", flag)
    w = ah.close_window(broker, symbols, now)
    assert (w.kind, w.start_at) == (kind, start)
    assert w.can_close_now == (kind in (ah.REGULAR, ah.POST_CLOSE, ah.OVERNIGHT))
    if kind == ah.CLOSED:
        assert w.message


def test_an_aware_moment_reads_in_new_york():
    # 03:00 New York on Thu 11-05 is 08:00 UTC (EST)
    assert ah.close_window(TT, SPXW, datetime(2026, 11, 5, 8, 0, tzinfo=UTC)).kind == ah.OVERNIGHT


def test_closed_messages(monkeypatch):
    monkeypatch.setattr(ah, "POST_CLOSE_ENABLED", True)
    assert ah.close_window(TT, EXPIRING, dt(4, 16, 5)).message == ah.EXPIRING_TODAY
    assert ah.close_window(TT, SPY, dt(4, 17, 10)).message == ah.MARKET_CLOSED
    assert ah.close_window("schwab", SPXW, dt(4, 22, 0)).message == ah.MARKET_CLOSED


def test_post_close_ships_off():
    assert ah.POST_CLOSE_ENABLED is False


def test_no_post_close_on_a_half_day(monkeypatch):
    monkeypatch.setattr(ah, "POST_CLOSE_ENABLED", True)
    # Fri 2026-11-27 closes at 13:00: 16:05 is the gap, not a post-close quarter hour
    assert ah.close_window(TT, SPXW, dt(27, 16, 5)).kind == ah.SCHEDULE


@pytest.mark.parametrize(
    "now,kind",
    [(dt(4, 10, 0), ah.REGULAR), (dt(4, 17, 10), ah.CLOSED), (dt(4, 22, 0), ah.CLOSED)],
)
def test_close_window_refuses_expired_options(now, kind):
    w = ah.close_window(TT, EXPIRING, now)
    assert w.kind == kind
    if kind == ah.CLOSED:
        assert w.message == ah.EXPIRED


def test_calendar_with_an_expired_front_leg_still_closes():
    # Thu 11-05 22:00 belongs to Fri: the front leg expired, the back leg trades
    assert ah.close_window(TT, CALENDAR, dt(5, 22, 0)).kind == ah.OVERNIGHT
    w = ah.close_window(TT, CALENDAR, dt(5, 17, 10))
    assert (w.kind, w.start_at) == (ah.SCHEDULE, et(5, 20, 16))


def test_every_leg_expired_refuses():
    legs = ["SPXW  261105C07750000", "SPXW  261105P07700000"]
    assert ah.close_window(TT, legs, dt(5, 22, 0)).message == ah.EXPIRED


def test_overnight_into_expiry_day_still_trades():
    # Wed 22:00 belongs to Thu 11-05: a leg expiring Thu still trades
    assert ah.close_window(TT, ["SPXW  261105C07750000"], dt(4, 22, 0)).kind == ah.OVERNIGHT
    assert ah.close_window(TT, ["SPXW  261105C07750000"], dt(5, 9, 26)).kind == ah.SCHEDULE


def test_an_unknown_calendar_year_refuses_off_hours_only():
    # The table covers 2026 and 2027 only: a 2028 night is refused, never guessed
    w = ah.close_window(TT, ["SPXW  281218C07000000"], datetime(2028, 3, 7, 22, 0))
    assert w.kind == ah.CLOSED and "2028" in w.message
    # and late December 2027 looks ten days ahead into 2028
    assert ah.close_window(TT, SPXW, datetime(2027, 12, 28, 22, 0)).kind == ah.CLOSED


def test_trading_day():
    assert ah.trading_day(dt(4, 22, 0)) == date(2026, 11, 5)
    assert ah.trading_day(dt(5, 3, 0)) == date(2026, 11, 5)
    assert ah.trading_day(dt(6, 21, 0)) == date(2026, 11, 9)
    assert ah.trading_day(dt(25, 21, 0)) == date(2026, 11, 27)


def test_next_start_is_the_open_plus_the_opening_pause():
    assert ah.next_start(dt(4, 17, 10)) == et(4, 20, 15) + timedelta(seconds=60)
    assert ah.next_start(dt(5, 9, 25)) == et(5, 9, 31)
    assert ah.starts_overnight(ah.next_start(dt(4, 17, 10)))
    assert not ah.starts_overnight(ah.next_start(dt(5, 9, 26)))


def test_capable():
    assert ah.capable(TT, SPXW)
    assert ah.capable("TastyTrade", ["XSP   261218P00580000", "RUTW  261218P02000000"])
    assert not ah.capable(TT, [*SPXW, *SPY])  # every leg
    assert not ah.capable(TT, [])
    assert not ah.capable("schwab", SPXW)
    assert not ah.capable(TT, ["./ESZ6 EW3X6 261120P5800"])  # futures


@pytest.mark.parametrize(
    "now,refused",
    [
        (dt(4, 10, 0), False),
        (dt(4, 16, 0), True),
        (dt(4, 22, 0), True),
        (dt(5, 9, 29, 59), True),
        (dt(7, 12, 0), True),
    ],
)
def test_stops_refused(now, refused):
    assert ah.stops_refused(TT, SPXW, now) is refused
    assert ah.stops_refused("schwab", SPXW, now) is False  # unchanged for Schwab
    assert ah.stops_refused(TT, SPY, now) is False  # and for stock options


def test_stops_due_from_093005_and_the_retry_from_0945():
    assert not ah.stops_due(dt(5, 9, 30, 4))
    assert ah.stops_due(dt(5, 9, 30, 5))
    assert not ah.retry_due(dt(5, 9, 44, 59))
    assert ah.retry_due(dt(5, 9, 45))
    assert not ah.stops_due(dt(7, 9, 31))  # Saturday
    assert not ah.stops_due(dt(5, 16, 1))


def test_when_words():
    assert ah.when_words(et(4, 20, 16), dt(4, 17, 10)) == "20:16 ET"
    assert ah.when_words(et(8, 20, 16), dt(6, 20, 30)) == "Sun 20:16 ET"
