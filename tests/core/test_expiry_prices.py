"""Choosing the price an expiring option settles at (pure)."""

from datetime import UTC, date, datetime
from decimal import Decimal as D

from optionsjourney.core.accounts import DailyClose
from optionsjourney.core.expiry_prices import PriceRead, last_price, official_close, pl_changed
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.quotes import Quote

DAY = date(2026, 10, 19)
AFTER = date(2026, 10, 20)


def _ms(hour: int, minute: int = 0, second: int = 0) -> int:
    return int(datetime(2026, 10, 19, hour, minute, second, tzinfo=MARKET_TZ).timestamp() * 1000)


def test_official_close_prefers_schwab_then_tastytrade_final_then_prev_close():
    schwab = [DailyClose("SPX", DAY, D("7690.73"))]
    assert official_close(DAY, AFTER, None, schwab) == PriceRead(
        D("7690.73"), "schwab daily close", True
    )
    item = {"close": "766.48", "close-price-type": "Final", "summary-date": "2026-10-19"}
    assert official_close(DAY, DAY, item, []).source == "tastytrade close"
    regular = {"close-price-type": "Regular", "last": "7690.73"}  # 10/19 SPX at 9 PM
    assert official_close(DAY, DAY, regular, []) is None
    prev = {"prev-close": "7690.73", "prev-close-date": "2026-10-19"}
    read = official_close(DAY, AFTER, prev, [])
    assert read == PriceRead(D("7690.73"), "tastytrade prev-close", True)


def test_a_schwab_daily_close_is_final_only_after_the_day():
    schwab = [DailyClose("SPX", DAY, D("7690.73"))]
    assert official_close(DAY, DAY, None, schwab) is None  # may be the in-progress bar
    item = {"close": "766.48", "close-price-type": "Final", "summary-date": "2026-10-19"}
    assert official_close(DAY, DAY, item, schwab).source == "tastytrade close"
    assert official_close(DAY, AFTER, item, schwab).source == "schwab daily close"


def test_official_close_ignores_other_days_and_bad_values():
    other = [DailyClose("SPX", date(2026, 10, 16), D("1"))]
    assert official_close(DAY, AFTER, None, other) is None
    bad = {"close": "n/a", "close-price-type": "Final", "summary-date": "2026-10-19"}
    assert official_close(DAY, AFTER, bad, []) is None
    stale = {"close": "5", "close-price-type": "Final", "summary-date": "2026-10-16"}
    assert official_close(DAY, AFTER, stale, []) is None


def test_index_last_from_either_broker_same_day_only():
    tt = {"last": "7690.73", "updated-at": "2026-10-19T20:44:15.285Z"}
    assert last_price("SPX", DAY, tt, None, None) == PriceRead(
        D("7690.73"), "tastytrade last", False
    )
    stale = {"last": "7659.44", "updated-at": "2026-10-16T20:15:00Z"}
    schwab = {"quote": {"lastPrice": 7690.8, "tradeTime": _ms(16, 0, 1)}}
    assert last_price("SPX", DAY, stale, schwab, None).source == "schwab last"
    assert last_price("SPX", DAY, stale, None, None) is None


def test_an_etf_uses_the_regular_session_price_never_after_hours():
    schwab = {
        "quote": {"lastPrice": 767.10},
        "regular": {"regularMarketLastPrice": 766.48, "regularMarketTradeTime": _ms(16)},
    }
    assert last_price("SPY", DAY, None, schwab, None) == PriceRead(
        D("766.48"), "schwab regular last", False
    )
    at = datetime(2026, 10, 19, 16, 0, tzinfo=MARKET_TZ)
    hub = Quote("SPY", None, None, D("766.48"), "tastytrade", datetime.now(UTC), last_at=at)
    rest = {"last": "767.10", "updated-at": "2026-10-19T23:00:00Z"}
    assert last_price("SPY", DAY, rest, None, hub).source == "tastytrade stream last"
    assert last_price("SPY", DAY, rest, None, None) is None  # REST last can be after hours


def test_pl_changed_at_one_dollar():
    assert not pl_changed(D("-212.50"), D("-211.51"))
    assert pl_changed(D("-212.50"), D("-211.50"))


def test_a_zero_or_negative_price_is_never_used():
    """A glitchy 0 from a broker would settle SPX at 0: no price instead."""
    zero_tt = {"last": "0", "updated-at": "2026-10-19T20:44:15.285Z"}
    zero_schwab = {"quote": {"lastPrice": 0.0, "tradeTime": _ms(16, 0, 1)}}
    assert last_price("SPX", DAY, zero_tt, zero_schwab, None) is None
    regular = {"regular": {"regularMarketLastPrice": -1, "regularMarketTradeTime": _ms(16)}}
    at = datetime(2026, 10, 19, 16, 0, tzinfo=MARKET_TZ)
    hub = Quote("SPY", None, None, D("0"), "tastytrade", datetime.now(UTC), last_at=at)
    assert last_price("SPY", DAY, None, regular, hub) is None
    assert official_close(DAY, AFTER, None, [DailyClose("SPX", DAY, D("0"))]) is None
    final = {"close": "0", "close-price-type": "Final", "summary-date": "2026-10-19"}
    assert official_close(DAY, AFTER, final, []) is None
    prev = {"prev-close": "-5", "prev-close-date": "2026-10-19"}
    assert official_close(DAY, AFTER, prev, []) is None
