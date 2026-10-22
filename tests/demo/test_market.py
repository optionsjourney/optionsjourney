import time as _time
from datetime import UTC, date, datetime, timedelta

from optionsjourney.core.black_scholes import bs_price
from optionsjourney.demo import levels
from optionsjourney.demo.market import PretendMarket, Tick

T0 = datetime(2026, 10, 21, 13, 40, tzinfo=UTC)  # 9:40 New York
OCC = "SPXW  261021P05800000"


def test_moves_realistically():
    m = PretendMarket(seed=7, start=T0)
    for i in range(390):
        m.advance_to(T0 + timedelta(minutes=i))
    move = abs(m.last("SPX") / 5800 - 1)
    assert 0.0005 < move < 0.03


def test_same_seed_same_path():
    a, b = PretendMarket(seed=3, start=T0), PretendMarket(seed=3, start=T0)
    a.advance_to(T0 + timedelta(minutes=30))
    b.advance_to(T0 + timedelta(minutes=30))
    assert a.last("SPX") == b.last("SPX")


def test_path_does_not_depend_on_how_it_is_advanced():
    a, b = PretendMarket(seed=3, start=T0), PretendMarket(seed=3, start=T0)
    a.advance_to(T0 + timedelta(minutes=10))
    for i in range(1, 11):
        b.advance_to(T0 + timedelta(minutes=i))
    assert a.last("NVDA") == b.last("NVDA")


def test_advance_returns_every_second_for_every_ticker():
    m = PretendMarket(seed=1, start=T0)
    ticks = m.advance_to(T0 + timedelta(seconds=10))
    assert len(ticks) == 10 * len(levels.START)
    assert isinstance(ticks[0], Tick) and ticks[0].at == T0 + timedelta(seconds=1)
    assert m.advance_to(T0) == []


def test_steer_reaches_the_level_on_time():
    m = PretendMarket(seed=1, start=T0)
    m.steer("SPX", 5840, by=T0 + timedelta(seconds=60))
    m.advance_to(T0 + timedelta(seconds=61))
    assert abs(m.last("SPX") - 5840) < 2


def test_later_steer_replaces_earlier():
    m = PretendMarket(seed=1, start=T0)
    m.steer("SPX", 5900, by=T0 + timedelta(seconds=60))
    m.steer("SPX", 5760, by=T0 + timedelta(seconds=30))
    m.advance_to(T0 + timedelta(seconds=31))
    assert abs(m.last("SPX") - 5760) < 2


def test_vix_moves_against_spx():
    m = PretendMarket(seed=1, start=T0)
    m.steer("SPX", 5800 * 0.99, by=T0 + timedelta(seconds=120))
    m.advance_to(T0 + timedelta(seconds=121))
    assert m.last("VIX") > 16.5
    assert 11 <= m.last("VIX") <= 40


def test_option_mark_matches_black_scholes():
    m = PretendMarket(seed=1, start=T0)
    years = (6 * 3600 + 20 * 60) / (252 * 6.5 * 3600)
    expected = bs_price(5800, 5800, years, 0.04, 0.15, "PUT")
    assert abs(m.option_mark(OCC, T0) - expected) / expected < 0.01


def test_otm_puts_carry_skew():
    m = PretendMarket(seed=1, start=T0)
    years = (6 * 3600 + 20 * 60) / (252 * 6.5 * 3600)
    flat = bs_price(5800, 5600, years, 0.04, 0.15, "PUT")
    assert m.option_mark("SPXW  261021P05600000", T0) > flat


def test_quote_spreads():
    m = PretendMarket(seed=1, start=T0)
    bid, ask, last = m.quote(OCC)
    assert bid < last < ask and ask - bid >= 0.05 - 1e-9
    bid, ask, last = m.quote("SPX")
    assert bid < last < ask and ask - bid < 0.05


def test_candles_have_history_before_the_start():
    m = PretendMarket(seed=1, start=T0)
    m.advance_to(T0 + timedelta(minutes=20))
    c = m.candles("SPX", 5, 10)
    assert len(c) == 10
    assert set(c[0]) == {"time", "open", "high", "low", "close", "volume"}
    assert all(x["high"] >= max(x["open"], x["close"]) for x in c)
    assert c[-1]["close"] == m.last("SPX")
    assert c[0]["time"] < T0.timestamp() * 1000
    assert len(m.candles("SPX", 1, 500)) == 500


def test_history_is_bounded():
    m = PretendMarket(seed=1, start=T0)
    m.advance_to(T0 + timedelta(hours=6))
    assert m.history_size() <= 30_000


def test_a_time_just_past_the_close_is_fine():
    m = PretendMarket(seed=1, start=T0)
    ticks = m.advance_to(datetime(2026, 10, 21, 20, 0, 0, 5, tzinfo=UTC))
    assert ticks[-1].at == datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    assert m.advance_to(datetime(2026, 10, 21, 21, 0, tzinfo=UTC)) == []


def test_fifteen_minute_jump_is_fast():
    m = PretendMarket(seed=1, start=T0)
    t = _time.perf_counter()
    m.advance_to(T0 + timedelta(minutes=15))
    assert _time.perf_counter() - t < 0.5


def test_steering_spx_carries_the_etfs_along():
    m = PretendMarket(seed=1, start=T0)
    m.steer("SPX", 5800 * 0.99, by=T0 + timedelta(seconds=60))
    m.advance_to(T0 + timedelta(seconds=61))
    spx = m.last("SPX") / 5800 - 1
    assert abs((m.last("SPY") / 579 - 1) - spx) < 0.0015
    qqq = m.last("QQQ") / 500 - 1
    assert qqq < 0 and abs(qqq - spx * 0.9 * 0.19 / 0.15) < 0.0015


def test_zero_dte_atm_put_is_worth_a_realistic_amount():
    m = PretendMarket(seed=1, start=T0)
    assert 18 < m.option_mark(OCC, T0) < 26


def test_steer_is_gradual():
    m = PretendMarket(seed=1, start=T0)
    m.steer("SPX", 5800 * 0.99, by=T0 + timedelta(seconds=60))
    ticks = [t for t in m.advance_to(T0 + timedelta(seconds=60)) if t.ticker == "SPX"]
    prices = [5800.0] + [t.price for t in ticks]
    steps = [abs(b - a) for a, b in zip(prices, prices[1:], strict=False)]
    assert max(steps) < 3 * (sum(steps) / len(steps))


def test_steer_with_a_deadline_already_past_does_not_snap():
    m = PretendMarket(seed=1, start=T0)
    m.advance_to(T0 + timedelta(seconds=30))
    before = m.last("SPX")
    m.steer("SPX", before + 50, by=T0)
    ticks = [t for t in m.advance_to(T0 + timedelta(seconds=36)) if t.ticker == "SPX"]
    prices = [before] + [t.price for t in ticks]
    steps = [abs(b - a) for a, b in zip(prices, prices[1:], strict=False)]
    assert abs(prices[-1] - (before + 50)) < 2
    assert max(steps) < 25


def test_option_mark_at_prices_a_given_spot():
    m = PretendMarket(seed=1, start=T0)
    assert m.now == T0
    assert m.option_mark_at(OCC, m.last("SPX"), T0) == m.option_mark(OCC)
    assert m.option_mark_at(OCC, 5700, T0) < m.option_mark_at(OCC, 5650, T0)


def test_prev_close_is_the_last_price_before_today():
    m = PretendMarket(seed=1, start=T0)
    m.advance_to(T0 + timedelta(minutes=5))
    close = m.prev_close("SPX")
    assert close != m.last("SPX") and abs(close / 5800 - 1) < 0.03


def test_vix_for_spx_is_about_the_vix_a_move_brings():
    m = PretendMarket(seed=1, start=T0)
    for level in (5800 * 1.01, 5800 * 0.99):
        before = m.last("VIX")
        expected = m.vix_for_spx(level)
        assert (expected < before) == (level > m.last("SPX"))  # up: VIX down, and back
        m.steer("SPX", level, by=m.now + timedelta(seconds=60))
        m.advance_to(m.now + timedelta(seconds=61))
        assert abs(m.last("VIX") - expected) < 0.25, (m.last("VIX"), expected)


def test_vix_for_spx_stays_in_the_vix_range():
    m = PretendMarket(seed=1, start=T0)
    assert m.vix_for_spx(5800 * 0.5) == 40.0 and m.vix_for_spx(5800 * 2) == 11.0


def test_vix9d_moves_with_vix():
    """Final review I-3: VIX9D for the bots' VIX9D/VIX filter, always VIX x 0.95."""
    m = PretendMarket(seed=7, start=T0)
    assert abs(m.last("VIX9D") - m.last("VIX") * levels.VIX9D_RATIO) < 1e-9
    m.advance_to(T0 + timedelta(minutes=20))
    assert abs(m.last("VIX9D") - m.last("VIX") * levels.VIX9D_RATIO) < 1e-9
    assert m.last("VIX") != 16.5  # it has moved, and VIX9D with it
    assert abs(m.prev_close("VIX9D") - m.prev_close("VIX") * levels.VIX9D_RATIO) < 1e-9
    bars = zip(m.candles("VIX9D", 1440, 3), m.candles("VIX", 1440, 3), strict=True)
    assert all(abs(a["close"] - b["close"] * levels.VIX9D_RATIO) < 1e-9 for a, b in bars)


def test_vix9d_leaves_the_other_paths_alone():
    without = {k: v for k, v in levels.START.items() if k != "VIX9D"}
    a, b = PretendMarket(seed=3, start=T0), PretendMarket(seed=3, start=T0, levels=without)
    a.advance_to(T0 + timedelta(minutes=30))
    b.advance_to(T0 + timedelta(minutes=30))
    assert all(a.last(t) == b.last(t) for t in without)
    assert a.candles("SPX", 1, 50) == b.candles("SPX", 1, 50)


def test_daily_closes_by_day():
    m = PretendMarket(seed=3, start=T0)
    closes = m.daily_closes("SPX", date(2026, 10, 12), date(2026, 10, 21))
    assert [d for d, _ in closes] == [date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19, 20, 21)]
    assert closes[-2][1] == m.prev_close("SPX")
    assert closes[-1][1] == m.last("SPX")
