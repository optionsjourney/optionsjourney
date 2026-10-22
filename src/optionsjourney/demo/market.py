"""The pretend market: made-up, deterministic prices that move like a real day.

Pure Python: it never reads the clock. Callers pass the times.
"""

from __future__ import annotations

import math
import random
import zlib
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from optionsjourney.core.black_scholes import bs_price
from optionsjourney.core.symbols import parse_occ
from optionsjourney.demo.clock import CLOSE, NY
from optionsjourney.demo.levels import CORRELATION, START, VIX9D_RATIO, VIX_BASE, VOL

RATE = 0.04
_STEP_YEARS = 1 / (252 * 6.5 * 3600)  # one second, in trading years
_SQRT_STEP = math.sqrt(_STEP_YEARS)
_JUMP_CHANCE = 1 / 3600  # about one jump an hour
_JUMP_SIZE = 0.003
_PULL = 1 / 7200  # per second: draws the price back toward its anchor
_VIX_NOISE = 0.0004
_VIX_BETA = -4.0
_VIX_LOW, _VIX_HIGH = 11.0, 40.0
_SECONDS_KEPT = 1800  # per ticker; older seconds live on as 1-minute bars
_HISTORY_DAYS = 2
_OPEN = time(9, 30)
_MIN_STEER_SECONDS = 5
_SPX_FOLLOWERS = ("SPY", "QQQ", "IWM")
_SESSION_SECONDS = 6.5 * 3600
_DAILY_MINUTES = 1440
_WEEKLY_MINUTES = 7 * 1440
_MONTHLY_MINUTES = 30 * 1440
_DAILY_HISTORY_DAYS = 504  # about two years of trading days


_VIX_FAMILY = ("VIX", "VIX9D")  # no path of their own: VIX follows SPX, VIX9D follows VIX


def _scaled(bars: list[list], ratio: float) -> list[list]:
    """Bars [t, o, h, l, c, v] with every price times ``ratio``."""
    return [[b[0], *(p * ratio for p in b[1:5]), b[5]] for b in bars]


@dataclass(frozen=True)
class Tick:
    ticker: str
    at: datetime
    price: float


@dataclass
class _Steer:
    level: float
    by: datetime


def _weekdays_before(day: date, count: int) -> list[date]:
    out: list[date] = []
    d = day
    while len(out) < count:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return sorted(out)


class PretendMarket:
    def __init__(
        self, seed: int = 1, start: datetime | None = None, levels: dict | None = None
    ) -> None:
        if start is None:
            raise ValueError("start is required")
        levels = dict(START if levels is None else levels)
        self._rng = random.Random(seed)
        self._now = start
        self._close = datetime.combine(start.astimezone(NY).date(), CLOSE, tzinfo=NY)
        self._p = {t: float(v) for t, v in levels.items()}
        self._anchor = dict(self._p)
        self._steer: dict[str, _Steer] = {}
        self._sec: dict[str, deque] = {t: deque(maxlen=_SECONDS_KEPT) for t in self._p}
        self._bars: dict[str, list[list]] = {t: [] for t in self._p}
        self._seed = seed
        self._daily_past: dict[str, list[list]] = {}
        self._make_history(seed, start)

    # ----- the path -----
    @property
    def now(self) -> datetime:
        """The time the path has reached."""
        return self._now

    def last(self, ticker: str) -> float:
        return self._p[ticker]

    def prev_close(self, ticker: str) -> float:
        """The last price before today's open: the previous session's close."""
        day = self._now.astimezone(NY).date()
        opens = int(datetime.combine(day, _OPEN, tzinfo=NY).timestamp())
        before = [b for b in self._bars[ticker] if b[0] < opens]
        return before[-1][4] if before else self._anchor[ticker]

    def steer(self, ticker: str, level: float, by: datetime) -> None:
        """Drift ``ticker`` to ``level`` by ``by`` (never sooner than 5 s from now).

        Steering SPX also carries SPY, QQQ and IWM along by the same percentage
        move scaled by their beta, so the index and its ETFs stay together.
        """
        by = max(by, self._now + timedelta(seconds=_MIN_STEER_SECONDS))
        self._steer[ticker] = _Steer(float(level), by)
        if ticker == "SPX":
            pct = level / self._p["SPX"] - 1
            for follower in _SPX_FOLLOWERS:
                if follower in self._p:
                    beta = CORRELATION[follower] * VOL[follower] / VOL["SPX"]
                    self._steer[follower] = _Steer(self._p[follower] * (1 + pct * beta), by)

    def advance_to(self, at: datetime) -> list[Tick]:
        at = min(at, self._close)
        steps = int((at - self._now).total_seconds())
        ticks: list[Tick] = []
        for _ in range(steps):
            self._now += timedelta(seconds=1)
            ticks.extend(self._step(self._now))
        return ticks

    def _step(self, t: datetime) -> list[Tick]:
        rng = self._rng
        zm = rng.gauss(0, 1)
        jump = 0.0
        if rng.random() < _JUMP_CHANCE:
            jump = _JUMP_SIZE * (0.5 + rng.random()) * (1 if rng.random() < 0.5 else -1)
        out: list[Tick] = []
        spx_pct = 0.0
        for tk, old in self._p.items():
            if tk in _VIX_FAMILY:
                continue
            rho = CORRELATION.get(tk, 0.6)
            z = rho * zm + math.sqrt(max(0.0, 1 - rho * rho)) * rng.gauss(0, 1)
            ret = VOL[tk] * _SQRT_STEP * z + jump * rho
            new = old * math.exp(ret)
            if tk not in self._steer:
                new = old * math.exp(ret - _PULL * (old / self._anchor[tk] - 1))
            new = self._apply_steer(tk, new, t)
            if tk == "SPX":
                spx_pct = new / old - 1
            out.append(self._set(tk, new, t))
        if "VIX" in self._p:
            old = self._p["VIX"]
            ret = _VIX_BETA * spx_pct + _VIX_NOISE * rng.gauss(0, 1)
            if "VIX" not in self._steer:
                ret -= _PULL * (old / self._anchor["VIX"] - 1)
            new = self._apply_steer("VIX", old * (1 + ret), t)
            out.append(self._set("VIX", min(_VIX_HIGH, max(_VIX_LOW, new)), t))
            if "VIX9D" in self._p:
                out.append(self._set("VIX9D", self._p["VIX"] * VIX9D_RATIO, t))
        return out

    def _apply_steer(self, tk: str, new: float, t: datetime) -> float:
        s = self._steer.get(tk)
        if s is None:
            return new
        remaining = (s.by - t).total_seconds()
        if remaining <= 0:
            del self._steer[tk]
            self._anchor[tk] = s.level
            return s.level
        return new + (s.level - self._p[tk]) / (remaining + 1)

    def _set(self, tk: str, price: float, t: datetime) -> Tick:
        self._p[tk] = price
        self._sec[tk].append((t, price))
        minute = int(t.timestamp()) // 60 * 60
        bars = self._bars[tk]
        if bars and bars[-1][0] == minute:
            b = bars[-1]
            b[2] = max(b[2], price)
            b[3] = min(b[3], price)
            b[4] = price
        else:
            bars.append([minute, price, price, price, price, self._volume(tk, minute)])
        return Tick(tk, t, price)

    @staticmethod
    def _volume(tk: str, minute: int) -> int:
        base = 90_000 if tk in ("SPY", "QQQ", "IWM") else 20_000
        return base + (minute // 60 * 2654435761 + len(tk) * 977) % base

    # ----- made-up history before the start -----
    def _make_history(self, seed: int, start: datetime) -> None:
        rng = random.Random(seed * 7919 + 13)
        day = start.astimezone(NY).date()
        minutes: list[int] = []
        for d in [*_weekdays_before(day, _HISTORY_DAYS), day]:
            end = datetime.combine(d, CLOSE, tzinfo=NY) if d != day else start
            t = datetime.combine(d, _OPEN, tzinfo=NY)
            while t < end:
                minutes.append(int(t.timestamp()))
                t += timedelta(minutes=1)
        first_live = int(start.timestamp()) // 60 * 60
        minutes = [m for m in minutes if m < first_live]
        for tk, level in self._p.items():
            if tk == "VIX9D":
                continue  # follows VIX, below
            vol = VOL.get(tk, 0.8)
            price = level
            bars: list[list] = []
            for m in reversed(minutes):
                r = vol * math.sqrt(60 * _STEP_YEARS) * rng.gauss(0, 1)
                prev = price * math.exp(-r)
                wick = abs(rng.gauss(0, 1)) * vol * math.sqrt(60 * _STEP_YEARS) * 0.4
                hi = max(prev, price) * (1 + wick)
                lo = min(prev, price) * (1 - wick)
                bars.append([m, prev, hi, lo, price, self._volume(tk, m)])
                price = prev
            bars.reverse()
            self._bars[tk] = bars
        if "VIX9D" in self._p and "VIX" in self._p:
            self._bars["VIX9D"] = _scaled(self._bars["VIX"], VIX9D_RATIO)

    def history_size(self) -> int:
        return sum(len(d) for d in self._sec.values()) + sum(len(b) for b in self._bars.values())

    def _daily_history(self, ticker: str) -> list[list]:
        """Made-up daily bars [day_start_s, o, h, l, c, v] for the two years
        before today, ending at the previous close. Built once, on first use."""
        past = self._daily_past.get(ticker)
        if past is not None:
            return past
        if ticker == "VIX9D":
            past = _scaled(self._daily_history("VIX"), VIX9D_RATIO)
            self._daily_past[ticker] = past
            return past
        rng = random.Random(self._seed * 104729 + zlib.crc32(ticker.encode()))
        today = self._now.astimezone(NY).date()
        days = _weekdays_before(today, _DAILY_HISTORY_DAYS)
        vol = VOL.get(ticker, 0.8)
        sigma = vol * math.sqrt(1 / 252)
        price = self.prev_close(ticker)
        past = []
        for d in reversed(days):
            prev = price * math.exp(-sigma * rng.gauss(0, 1))
            wick = abs(rng.gauss(0, 1)) * sigma * 0.5
            start = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
            past.append(
                [
                    start,
                    prev,
                    max(prev, price) * (1 + wick),
                    min(prev, price) * (1 - wick),
                    price,
                    self._volume(ticker, start) * 390,
                ]
            )
            price = prev
        past.reverse()
        self._daily_past[ticker] = past
        return past

    def _daily_bars(self, ticker: str) -> list[list]:
        """The made-up past plus today's bar, built from the live path."""
        today = self._now.astimezone(NY).date()
        midnight = int(datetime(today.year, today.month, today.day, tzinfo=UTC).timestamp())
        opens = int(datetime.combine(today, _OPEN, tzinfo=NY).timestamp())
        todays = [b for b in self._bars[ticker] if b[0] >= opens]
        bars = list(self._daily_history(ticker))
        if todays:
            bars.append(
                [
                    midnight,
                    todays[0][1],
                    max(b[2] for b in todays),
                    min(b[3] for b in todays),
                    todays[-1][4],
                    sum(b[5] for b in todays),
                ]
            )
        return bars

    def daily_closes(self, ticker: str, start: date, end: date) -> list[tuple[date, float]]:
        """(day, close) from ``start`` to ``end``, today's bar so far included: the
        daily history Schwab's price history gives. KeyError for an unknown ticker."""
        if ticker not in self._p:
            raise KeyError(ticker)
        out = []
        for b in self._daily_bars(ticker):
            day = datetime.fromtimestamp(b[0], UTC).date()
            if start <= day <= end:
                out.append((day, b[4]))
        return out

    @staticmethod
    def _bucket(minutes: int, start: int) -> int:
        d = datetime.fromtimestamp(start, UTC).date()
        if minutes >= _MONTHLY_MINUTES:
            first = d.replace(day=1)
        elif minutes >= _WEEKLY_MINUTES:
            first = d - timedelta(days=d.weekday())
        else:
            first = d
        return int(datetime(first.year, first.month, first.day, tzinfo=UTC).timestamp())

    def candles(self, ticker: str, minutes: int, count: int) -> list[dict]:
        daily = int(minutes) >= _DAILY_MINUTES
        size = max(1, int(minutes)) * 60
        groups: dict[int, list] = {}
        source = self._daily_bars(ticker) if daily else self._bars[ticker]
        for b in source:
            key = self._bucket(int(minutes), b[0]) if daily else b[0] // size * size
            g = groups.get(key)
            if g is None:
                groups[key] = [b[1], b[2], b[3], b[4], b[5]]
            else:
                g[1] = max(g[1], b[2])
                g[2] = min(g[2], b[3])
                g[3] = b[4]
                g[4] += b[5]
        keys = sorted(groups)[-count:] if count > 0 else []
        return [
            {
                "time": k * 1000,
                "open": groups[k][0],
                "high": groups[k][1],
                "low": groups[k][2],
                "close": groups[k][3],
                "volume": groups[k][4],
            }
            for k in keys
        ]

    def vix_for_spx(self, level: float) -> float:
        """About the VIX once SPX has moved to ``level``: the path's own beta, without
        its noise or pull (what a steer's options are priced with ahead of it)."""
        vix = self._p.get("VIX", VIX_BASE)
        moved = vix * (level / self._p["SPX"]) ** _VIX_BETA
        return min(_VIX_HIGH, max(_VIX_LOW, moved))

    # ----- options -----
    def option_mark(self, occ: str, at: datetime | None = None) -> float:
        """The option's price now (or at ``at``), from the current spot and VIX."""
        parsed = parse_occ(occ)
        if parsed is None:
            raise ValueError(f"not an OCC option symbol: {occ!r}")
        return self.option_mark_at(occ, self._p[parsed[0]], at or self._now)

    def option_mark_at(
        self, occ: str, spot: float, at: datetime, vix: float | None = None
    ) -> float:
        """The option's price for a given spot, time and VIX (default: VIX now).
        Pure: how the pretend broker prices a spread at each tick of a jump."""
        strike, years, vol, put_call = self.option_inputs(occ, spot, at, vix)
        return bs_price(spot, strike, years, RATE, vol, put_call)

    def option_inputs(
        self, occ: str, spot: float, at: datetime, vix: float | None = None
    ) -> tuple[float, float, float, str]:
        """(strike, trading years left, volatility, "PUT"/"CALL") that price the
        option: the one place the vol and time basis live, for marks and Greeks."""
        parsed = parse_occ(occ)
        if parsed is None:
            raise ValueError(f"not an OCC option symbol: {occ!r}")
        underlying, d = parsed
        strike = float(d.strike)
        years = self._years_to(d.expiration, at)
        if vix is None:
            vix = self._p.get("VIX", VIX_BASE)
        vol = VOL[underlying] * (vix / VIX_BASE)
        if d.put_call == "PUT" and strike < spot:
            vol += min(0.05, 0.5 * (1 - strike / spot))
        return strike, years, vol, d.put_call

    @staticmethod
    def _years_to(expiration: date, at: datetime) -> float:
        """Trading-time years to the expiry close: the same basis as the path."""
        local = at.astimezone(NY)
        today = local.date()
        if expiration < today:
            return 0.0
        close = datetime.combine(today, CLOSE, tzinfo=NY)
        opens = datetime.combine(today, _OPEN, tzinfo=NY)
        seconds = max(0.0, (close - max(local, opens)).total_seconds())
        d = today
        while d < expiration:
            d += timedelta(days=1)
            if d.weekday() < 5:
                seconds += _SESSION_SECONDS
        return seconds / (252 * _SESSION_SECONDS)

    def quote(self, key: str) -> tuple[float, float, float]:
        if key in self._p:
            last = self._p[key]
            return round(last - 0.005, 4), round(last + 0.005, 4), last
        mark = self.option_mark(key)
        spread = max(0.05, 0.01 * mark)
        return max(0.0, mark - spread / 2), mark + spread / 2, mark
