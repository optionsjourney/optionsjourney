"""The chart grid's bar format and candle book (#25), ported from Tracker2's
services/chart_feed.py so its chart pages can be used as they are."""

from datetime import UTC, datetime

from optionsjourney.core.chart_bars import (
    REMOVE,
    SNAPSHOT_BEGIN,
    SNAPSHOT_END,
    CandleBook,
    chart_bar,
    dxlink_period,
    parse_event_symbol,
    valid_feed_symbol,
)


def ms(*args):
    return int(datetime(*args, tzinfo=UTC).timestamp() * 1000)


def test_intraday_bars_are_new_york_wall_clock_written_as_utc():
    # 14:30 UTC on 2026-10-09 is 10:30 New York (EDT, UTC-4)
    bar = chart_bar("5m", ms(2026, 10, 9, 14, 30), 1, 2, 0.5, 1.5, 100)
    assert bar == {
        "time": int(datetime(2026, 10, 9, 10, 30, tzinfo=UTC).timestamp()),
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 100.0,
    }


def test_equity_bars_outside_the_regular_session_are_dropped_futures_kept():
    pre_market = ms(2026, 10, 9, 13, 0)  # 09:00 New York
    at_close = ms(2026, 10, 9, 20, 0)  # 16:00 New York
    assert chart_bar("5m", pre_market, 1, 1, 1, 1, 0) is None
    assert chart_bar("5m", at_close, 1, 1, 1, 1, 0) is None
    assert chart_bar("5m", pre_market, 1, 1, 1, 1, 0, full_session=True) is not None


def test_daily_bars_keep_their_time_and_bad_opens_are_dropped():
    day = ms(2026, 10, 9, 0, 0)
    assert chart_bar("1d", day, 5, 6, 4, 5, None)["time"] == day // 1000
    assert chart_bar("1d", day, 5, 6, 4, 5, None)["volume"] == 0.0
    assert chart_bar("1d", day, 0, 6, 4, 5, 1) is None
    assert chart_bar("1d", day, "NaN", 6, 4, 5, 1) is None


def test_event_symbols_and_periods():
    assert parse_event_symbol("SPX{=5m,tho=true}") == ("SPX", "5m")
    assert parse_event_symbol("SPY{=d}") == ("SPY", "1d")
    assert parse_event_symbol("SPY{=h,a=s,tho=true}") == ("SPY", "1h")
    assert parse_event_symbol("/ESZ26:XCME{=mo}") == ("/ESZ26:XCME", "1mo")
    assert parse_event_symbol("SPY") is None
    assert dxlink_period("1h") == "h,a=s"
    assert dxlink_period("4h") == "4h,a=s"
    assert dxlink_period("5m") == "5m"
    assert valid_feed_symbol("SPY") and valid_feed_symbol("/ESZ26:XCME")
    assert not valid_feed_symbol("/ES") and not valid_feed_symbol("spy")


def test_book_swaps_in_a_snapshot_then_moves_the_forming_bar():
    book = CandleBook("1d")
    d1, d2 = ms(2026, 10, 8), ms(2026, 10, 9)
    assert book.apply(SNAPSHOT_BEGIN, d2, 2, 2, 2, 2, 1) is None
    assert book.apply(SNAPSHOT_END, d1, 1, 1, 1, 1, 1) == "snapshot"
    assert [b["time"] for b in book.bars()] == [d1 // 1000, d2 // 1000]
    assert book.apply(0, d2, 2, 3, 2, 3, 5) == "update"
    assert book.last_changed["close"] == 3.0
    assert book.apply(REMOVE, d2, 0, 0, 0, 0, 0) == "snapshot"
    assert [b["time"] for b in book.bars()] == [d1 // 1000]


def test_book_keeps_serving_old_bars_while_a_new_snapshot_comes_in():
    book = CandleBook("1d")
    d1, d2 = ms(2026, 10, 8), ms(2026, 10, 9)
    book.apply(SNAPSHOT_BEGIN | SNAPSHOT_END, d1, 1, 1, 1, 1, 1)
    book.apply(SNAPSHOT_BEGIN, d2, 2, 2, 2, 2, 1)
    assert [b["time"] for b in book.bars()] == [d1 // 1000]
