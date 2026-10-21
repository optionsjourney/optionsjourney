"""Candles for the charts page (#25): the bar type, session filtering,
rolling bars up, and the overlays (SMA, EMA, VWAP). Pure: the brokers'
history comes in through services/candles.py."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from optionsjourney.core.market import MARKET_TZ, market_date

INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
SESSION_OPEN, SESSION_CLOSE = time(9, 30), time(16, 0)


@dataclass(frozen=True)
class Candle:
    time: datetime  # the bar's start, timezone-aware
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def dxlink_candle_symbol(symbol: str, interval: str) -> str:
    """DXLink candle symbol: "SPY{=5m}", "SPX{=h,a=s}". Hour bars are aligned to the 09:30
    open ("a=s"), as Tracker2 does."""
    period = "h,a=s" if interval == "1h" else interval
    return f"{symbol}{{={period}}}"


def regular_session(candles: Iterable[Candle]) -> list[Candle]:
    """Intraday bars from 09:30 to before 16:00, New York time."""
    kept = []
    for candle in candles:
        local = candle.time.astimezone(MARKET_TZ).time()
        if SESSION_OPEN <= local < SESSION_CLOSE:
            kept.append(candle)
    return kept


def aggregate(candles: list[Candle], minutes: int) -> list[Candle]:
    """Roll bars up into `minutes`-long ones counted from each day's 09:30."""
    groups: dict[datetime, list[Candle]] = {}
    for candle in candles:
        local = candle.time.astimezone(MARKET_TZ)
        opened = datetime.combine(local.date(), SESSION_OPEN, tzinfo=MARKET_TZ)
        start = opened + timedelta(
            minutes=((local - opened).total_seconds() // 60 // minutes) * minutes
        )
        groups.setdefault(start, []).append(candle)
    return [
        Candle(
            start,
            bars[0].open,
            max(b.high for b in bars),
            min(b.low for b in bars),
            bars[-1].close,
            sum((b.volume for b in bars), Decimal("0")),
        )
        for start, bars in sorted(groups.items())
    ]


def sma(values: list[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = []
    for index in range(len(values)):
        window = values[max(0, index - period + 1) : index + 1]
        out.append(sum(window, Decimal("0")) / period if len(window) == period else None)
    return out


def ema(values: list[Decimal], period: int) -> list[Decimal | None]:
    """Exponential average, seeded with the simple average of the first
    `period` values; k = 2 / (period + 1)."""
    out: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = Decimal(2) / (period + 1)
    current = sum(values[:period], Decimal("0")) / period
    out[period - 1] = current
    for index in range(period, len(values)):
        current = current + k * (values[index] - current)
        out[index] = current
    return out


def vwap(candles: list[Candle]) -> list[Decimal | None]:
    """Volume-weighted average of the typical price, restarting each day."""
    out: list[Decimal | None] = []
    day, weighted, volume = None, Decimal("0"), Decimal("0")
    for candle in candles:
        if market_date(candle.time) != day:
            day, weighted, volume = market_date(candle.time), Decimal("0"), Decimal("0")
        typical = (candle.high + candle.low + candle.close) / 3
        weighted += typical * candle.volume
        volume += candle.volume
        out.append(weighted / volume if volume else typical)
    return out
