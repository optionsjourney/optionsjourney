from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from optionsjourney.bots import market_filters as mf
from optionsjourney.bots.definition import MoveLimits
from optionsjourney.core.candles import Candle
from optionsjourney.core.market import MARKET_TZ

D = Decimal
DAY = date(2026, 10, 13)  # a Tuesday


def et(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=MARKET_TZ)


def bar(at, open_, close=None):
    close = open_ if close is None else close
    return Candle(at, D(open_), D(open_), D(open_), D(close), D(0))


def test_each_max_caps_only_its_own_direction():
    limits = MoveLimits(max_up=D("3"), max_down=D("2"))
    assert mf.move_failures(D("4"), limits) == ["max up 3.00%"]
    assert mf.move_failures(D("-2.5"), limits) == ["max down 2.00%"]
    assert mf.move_failures(D("-1"), MoveLimits(max_up=D("0.5"))) == []
    assert mf.move_failures(D("3"), limits) == []  # inclusive


def test_a_min_demands_a_move_that_way():
    assert mf.move_failures(D("0.29"), MoveLimits(min_up=D("0.3"))) == ["min up 0.30%"]
    assert mf.move_failures(D("-0.5"), MoveLimits(min_up=D("0.3"))) == ["min up 0.30%"]
    assert mf.move_failures(D("0.33"), MoveLimits(min_up=D("0.3"))) == []
    assert mf.move_failures(D("-1"), MoveLimits(min_down=D("1"))) == []
    assert mf.move_failures(D("0"), MoveLimits(min_down=D("1"))) == ["min down 1.00%"]


def test_with_both_mins_either_is_enough():
    limits = MoveLimits(min_up=D("1"), min_down=D("1"))
    assert mf.move_failures(D("1.5"), limits) == []
    assert mf.move_failures(D("-1.5"), limits) == []
    assert mf.move_failures(D("0.2"), limits) == ["min up 1.00% or min down 1.00%"]


def test_no_limits_never_fail():
    assert mf.move_failures(D("12"), MoveLimits()) == []


def test_the_overnight_move_and_its_detail():
    ok, detail = mf.evaluate_overnight(D("15.00"), D("15.60"), MoveLimits(max_up=D("3")))
    assert not ok
    assert detail == "VIX 15.00 -> 15.60 (+4.00%); outside max up 3.00%"
    ok, detail = mf.evaluate_overnight(D("15.00"), D("15.00"), MoveLimits(max_up=D("3")))
    assert ok and detail == "VIX 15.00 -> 15.00 (0.00%)"


def test_vix_level_bounds_are_inclusive():
    assert mf.evaluate_level(D("20"), D("12"), D("20")) == (True, "VIX 20")
    assert mf.evaluate_level(D("21.3"), None, D("20")) == (False, "VIX 21.3; above max 20")
    assert mf.evaluate_level(D("11"), D("12"), None) == (False, "VIX 11; below min 12")


def test_the_ratio_is_vix9d_over_vix():
    ok, detail = mf.evaluate_ratio(D("16.00"), D("14.50"), None, D("0.9"))
    assert not ok and detail == "VIX9D/VIX 14.50 / 16.00 = 0.906; above max 0.9"
    assert mf.evaluate_ratio(D("16"), D("14.4"), D("0.8"), D("0.9"))[0]  # 0.9 exactly passes


def test_option_omegas_three_day_sma_includes_the_current_price():
    # A 3-day SMA at 1 PM: the 1 PM price plus the two previous closes, over 3
    assert mf.sma(D("103"), [D("100"), D("97"), D("50")], 3) == D("100")
    assert mf.sma(D("103"), [D("100")], 3) is None


def test_sma_above_below_and_compare():
    closes = [D("100"), D("98"), D("96"), D("94")]  # newest first
    assert mf.evaluate_sma("above", D("101"), closes, 2, None) == (
        True,
        "price 101.00 > SMA(2d) 100.50",
    )
    assert not mf.evaluate_sma("below", D("101"), closes, 2, None)[0]
    # Equal fails both ways
    assert not mf.evaluate_sma("above", D("100"), [D("100")], 2, None)[0]
    assert not mf.evaluate_sma("below", D("100"), [D("100")], 2, None)[0]
    # Rising closes: the 2-day average is above the 4-day one
    ok, detail = mf.evaluate_sma("compare", D("101"), closes, 2, 4)
    assert ok and detail == "SMA(2d) 100.50 > SMA(4d) 98.75"


def test_sma_fails_closed_on_too_little_history_or_a_bad_setting():
    assert mf.evaluate_sma("above", D("101"), [], 5, None) == (
        False,
        "not enough history for SMA(5d)",
    )
    assert mf.evaluate_sma("sideways", D("101"), [D("1")], 2, None)[0] is False
    assert mf.evaluate_sma("above", D("101"), [D("1")], None, None) == (
        False,
        "SMA period not set",
    )
    assert mf.evaluate_sma("compare", D("101"), [D("1")], 2, None)[0] is False


def test_ema_uses_the_current_price_as_the_last_value():
    # EMA(3) of 1, 2, 3, then the price 4: seed 2, then 4*0.5 + 2*0.5 = 3
    ok, detail = mf.evaluate_ema("above", D("4"), [D("1"), D("2"), D("3")], 3, None)
    assert ok and detail == "price 4.00 > EMA(3m) 3.00"
    assert mf.evaluate_ema("above", D("4"), [D("1")], 3, None) == (
        False,
        "not enough history for EMA(3m)",
    )


def test_intraday_move_names_the_symbol():
    ok, detail = mf.evaluate_move("SPX", D("5712.40"), D("5731.10"), MoveLimits(min_up=D("0.3")))
    assert ok and detail == "SPX 5712.40 -> 5731.10 (+0.33%)"
    ok, detail = mf.evaluate_move("SPX", D("5000"), D("4975"), MoveLimits(min_up=D("0.3")))
    assert not ok and detail.endswith("; outside min up 0.30%")


def test_the_open_is_the_first_bar_at_or_after_0930():
    bars = [bar(et(DAY, 9, 29), "9"), bar(et(DAY, 9, 31), "11"), bar(et(DAY, 9, 30), "10")]
    assert mf.first_rth_open(bars, DAY) == D("10")
    assert mf.first_rth_open([bar(et(DAY, 9, 29), "9")], DAY) is None
    assert mf.first_rth_open([bar(et(DAY - timedelta(days=1), 9, 30), "9")], DAY) is None


def test_prior_daily_closes_leave_out_todays_bar_by_date():
    def daily(day, close):
        return bar(datetime(day.year, day.month, day.day, tzinfo=UTC), "1", close)

    bars = [daily(DAY, "9"), daily(DAY - timedelta(days=4), "6"), daily(DAY - timedelta(1), "8")]
    assert mf.prior_daily_closes(bars, DAY) == [D("8"), D("6")]


def test_completed_minute_closes_are_the_previous_session_and_today():
    yesterday, older = DAY - timedelta(days=1), DAY - timedelta(days=2)
    now = et(DAY, 9, 32) + timedelta(seconds=30)
    bars = [
        bar(et(older, 15, 59), "1", "1"),  # two sessions back: dropped
        bar(et(yesterday, 15, 59), "2", "2"),
        bar(et(yesterday, 16, 5), "3", "3"),  # after the close: dropped
        bar(et(DAY, 9, 31), "5", "5"),
        bar(et(DAY, 9, 30), "4", "4"),
        bar(et(DAY, 9, 32), "6", "6"),  # still forming at 09:32:30: dropped
    ]
    assert mf.completed_rth_closes(bars, DAY, now) == [D("2"), D("4"), D("5")]


def test_xsp_reads_spx():
    assert mf.dx_symbol("XSP") == "SPX"
    assert mf.dx_symbol("spx") == "SPX"


def test_without_a_0930_bar_there_is_no_open():
    # A later bar's open is never taken for 09:30's (a missing first bar fails closed).
    assert mf.first_rth_open([bar(et(DAY, 9, 31), "11"), bar(et(DAY, 9, 32), "12")], DAY) is None
    assert mf.first_rth_open([bar(et(DAY, 9, 30) + timedelta(seconds=30), "11")], DAY) is None
