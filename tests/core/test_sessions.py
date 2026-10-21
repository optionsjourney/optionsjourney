"""Trading sessions for pricing (#26), as Tracker2's services/sessions.py."""

from datetime import date, datetime

from optionsjourney.core.sessions import (
    calendar_covers,
    next_open,
    next_session_after,
    session_now,
)


def test_stocks_trade_only_in_the_regular_session():
    assert session_now("AAPL", datetime(2026, 10, 9, 10, 0)) == "regular"
    assert session_now("AAPL", datetime(2026, 10, 9, 16, 0)) is None
    assert session_now("AAPL", datetime(2026, 11, 27, 13, 30)) is None  # half day


def test_cboe_indexes_have_curb_and_overnight_sessions():
    assert session_now("SPX", datetime(2026, 10, 9, 16, 30)) == "curb"
    assert session_now("SPX", datetime(2026, 10, 12, 3, 0)) == "overnight"  # Monday early
    assert session_now("SPX", datetime(2026, 10, 11, 20, 30)) == "overnight"  # Sunday evening
    assert session_now("SPX", datetime(2026, 10, 10, 20, 30)) is None  # Saturday evening
    assert session_now("SPX", datetime(2026, 11, 25, 20, 30)) is None  # before Thanksgiving


def test_futures_follow_cme_hours():
    assert session_now("/ES", datetime(2026, 10, 9, 16, 59)) == "futures"
    assert session_now("/ES", datetime(2026, 10, 9, 17, 30)) is None  # Friday after close
    assert session_now("/ES", datetime(2026, 10, 11, 18, 0)) == "futures"  # Sunday open
    assert session_now("/ES", datetime(2026, 10, 12, 17, 30)) is None  # daily halt


def test_next_open():
    assert next_open("AAPL", datetime(2026, 10, 10, 12, 0)) == datetime(2026, 10, 12, 9, 30)
    assert next_open("SPX", datetime(2026, 10, 10, 12, 0)) == datetime(2026, 10, 11, 20, 15)
    assert next_open("AAPL", datetime(2026, 10, 9, 10, 0)) is None


def test_the_holiday_table_covers_only_the_years_it_lists():
    assert calendar_covers(date(2026, 6, 1)) and calendar_covers(date(2027, 12, 31))
    assert not calendar_covers(date(2030, 6, 1)) and not calendar_covers(date(2025, 6, 1))


# Slice 5: NYSE's fetched calendar layered over the table (Tracker2's _build_calendar)

import pytest  # noqa: E402

from optionsjourney.core import sessions  # noqa: E402


@pytest.fixture
def fetched():
    yield sessions.use_fetched_calendar
    sessions.use_fetched_calendar([], {})


def test_fetched_years_replace_the_table_and_other_years_fall_back(fetched):
    from datetime import time

    # 2027 and 2028 fetched: a made-up 2027-03-01 closure, Good Friday 2027 not listed
    fetched([date(2027, 3, 1), date(2028, 1, 17)], {date(2028, 7, 3): time(13, 0)})
    assert sessions.session_close(date(2027, 3, 1)) is None
    assert sessions.session_close(date(2027, 3, 26)) == time(16, 0)  # the table's, replaced
    assert sessions.session_close(date(2028, 1, 17)) is None
    assert sessions.session_close(date(2028, 7, 3)) == time(13, 0)
    assert sessions.session_close(date(2026, 12, 25)) is None  # 2026 falls back to the table
    assert sessions.session_close(date(2026, 11, 27)) == time(13, 0)
    assert sessions.calendar_covers(date(2028, 6, 1))
    assert not sessions.calendar_covers(date(2029, 6, 1))
    assert sessions.calendar_through() == 2028


def test_without_a_fetched_calendar_the_table_decides(fetched):
    from datetime import time

    fetched([], {})
    assert sessions.session_close(date(2027, 3, 26)) is None
    assert sessions.calendar_through() == 2027
    assert sessions.session_close(date(2027, 3, 29)) == time(16, 0)


def test_next_session_after_skips_the_weekend():
    assert next_session_after(date(2026, 10, 16)) == date(2026, 10, 19)
    assert next_session_after(date(2026, 11, 25)) == date(2026, 11, 27)  # Thanksgiving
