from datetime import UTC, datetime, timedelta
from decimal import Decimal

from optionsjourney.core.candles import (
    Candle,
    aggregate,
    dxlink_candle_symbol,
    ema,
    regular_session,
    sma,
    vwap,
)

D = Decimal
OPEN = datetime(2026, 10, 9, 13, 30, tzinfo=UTC)  # 09:30 New York


def bar(minutes, o, h, low, c, v=100):
    return Candle(OPEN + timedelta(minutes=minutes), D(o), D(h), D(low), D(c), D(v))


def test_dxlink_candle_symbols():
    assert dxlink_candle_symbol("SPY", "5m") == "SPY{=5m}"
    assert dxlink_candle_symbol("SPX", "1h") == "SPX{=h,a=s}"  # hours start at the 9:30 open
    assert dxlink_candle_symbol("SPY", "1d") == "SPY{=1d}"


def test_regular_session_drops_pre_and_after_hours():
    candles = [bar(-30, 1, 1, 1, 1), bar(0, 1, 1, 1, 1), bar(385, 1, 1, 1, 1), bar(390, 1, 1, 1, 1)]
    assert [c.time for c in regular_session(candles)] == [OPEN, OPEN + timedelta(minutes=385)]


def test_aggregate_30m_into_hours_from_the_open():
    candles = [bar(0, 10, 12, 9, 11, 100), bar(30, 11, 13, 10, 12, 50), bar(60, 12, 12, 8, 9, 25)]
    hours = aggregate(candles, 60)
    assert [(c.time, c.open, c.high, c.low, c.close, c.volume) for c in hours] == [
        (OPEN, D(10), D(13), D(9), D(12), D(150)),
        (OPEN + timedelta(hours=1), D(12), D(12), D(8), D(9), D(25)),
    ]


def test_sma_and_ema():
    closes = [D(n) for n in (1, 2, 3, 4, 5)]
    assert sma(closes, 3) == [None, None, D(2), D(3), D(4)]
    values = ema(closes, 3)
    assert values[:2] == [None, None] and values[2] == D(2)  # seeded with the SMA
    assert values[3] == D(3) and values[4] == D(4)  # k = 0.5: 2 + 0.5*(4-2) = 3, 3 + 0.5*(5-3) = 4


def test_vwap_restarts_each_day():
    day1 = [bar(0, 1, 2, 1, 1.5, 100), bar(5, 1, 3, 1, 2, 300)]
    day2 = [Candle(OPEN + timedelta(days=1), D(10), D(10), D(10), D(10), D(50))]
    values = vwap(day1 + day2)
    # typical prices (h+l+c)/3: 1.5 and 2; weighted (150 + 600) / 400 = 1.875
    assert values[0] == D("1.5") and values[1] == D("1.875") and values[2] == D(10)
