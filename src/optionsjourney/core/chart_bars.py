"""The chart grid's bars (#25), as Tracker2's chart pages expect them
(ported from its services/chart_feed.py, so its JavaScript runs unchanged):

- Intraday bar times are New York wall clock written as a UTC epoch
  (a 10:30 New York bar is 10:30 UTC), in seconds.
- Daily, weekly and monthly bars are 00:00 UTC of their first session date.
- Stocks and indexes keep 09:30 to before 16:00 New York; futures keep every
  hour they trade.

DXLink sends a candle subscription's history as a snapshot (the first event
flagged SNAPSHOT_BEGIN, the last SNAPSHOT_END or SNAPSHOT_SNIP); after that,
events move the forming bar. A reconnect sends the snapshot again: the book
keeps serving the old bars until the new snapshot is complete."""

import math
import re
from datetime import datetime, timedelta

from optionsjourney.core.market import MARKET_TZ

REMOVE = 0x02
SNAPSHOT_BEGIN = 0x04
SNAPSHOT_END = 0x08
SNAPSHOT_SNIP = 0x10

# interval -> how far back the history goes
HISTORY = {
    "1m": timedelta(days=4),
    "5m": timedelta(days=10),
    "15m": timedelta(days=14),
    "30m": timedelta(days=21),
    "1h": timedelta(days=45),
    "4h": timedelta(days=120),
    "1d": timedelta(days=760),
    "1w": timedelta(days=365 * 5),
    "1mo": timedelta(days=365 * 20),
}
DAILY_OR_LONGER = {"1d", "1w", "1mo"}
# Hour bars aligned to the 09:30 open ("a=s"). DXLink answers nothing for "1h,a=s".
_DXLINK_PERIOD = {"1h": "h,a=s", "4h": "4h,a=s"}
# Stocks and indexes, and futures by DXLink contract ("/ESZ26:XCME"); a root like /ES has none
_FEED_SYMBOL = re.compile(r"^([A-Z][A-Z0-9.]{0,9}|/[A-Z0-9]{2,8}:[A-Z]{3,4})$")
_EVENT_SYMBOL = re.compile(r"^([^{]+)\{=([0-9]*)([a-z]+)")
_OPEN_MINUTE, _CLOSE_MINUTE = 9 * 60 + 30, 16 * 60


def dxlink_period(interval: str) -> str:
    return _DXLINK_PERIOD.get(interval, interval)


def valid_feed_symbol(symbol: str) -> bool:
    return bool(_FEED_SYMBOL.match(symbol or ""))


def parse_event_symbol(event_symbol: str | None) -> tuple[str, str] | None:
    """ "SPX{=5m,tho=true}" -> ("SPX", "5m"). DXLink writes "1d" back as "{=d",
    so a bare unit gets its 1."""
    found = _EVENT_SYMBOL.match(event_symbol or "")
    if not found:
        return None
    return found.group(1), f"{found.group(2) or '1'}{found.group(3)}"


def _number(value) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return None if math.isnan(number) else number


def chart_bar(
    interval: str, time_ms, open_, high, low, close, volume, full_session: bool = False
) -> dict | None:
    """One candle as a chart bar, or None when it isn't a regular-session bar
    with a real open."""
    o, h, lo, c = _number(open_), _number(high), _number(low), _number(close)
    if not o or o <= 0 or h is None or lo is None or c is None:
        return None
    seconds = int(time_ms) // 1000
    if interval not in DAILY_OR_LONGER:
        wall = datetime.fromtimestamp(seconds, MARKET_TZ)
        minute = wall.hour * 60 + wall.minute
        if not full_session and not _OPEN_MINUTE <= minute < _CLOSE_MINUTE:
            return None
        seconds += int(wall.utcoffset().total_seconds())
    return {
        "time": seconds,
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "volume": _number(volume) or 0.0,
    }


class CandleBook:
    """The bars of one (symbol, interval) subscription."""

    def __init__(self, interval: str, full_session: bool = False):
        self.interval = interval
        self.full_session = full_session
        self._bars: dict[int, dict] = {}
        self._pending: dict[int, dict] | None = None  # a snapshot coming in
        self.ready = False
        self.last_changed: dict | None = None

    def apply(self, flags, time_ms, open_, high, low, close, volume) -> str | None:
        """ "snapshot" when the whole history should be (re)sent, "update" when
        last_changed should be, else None."""
        flags = int(flags or 0)
        if flags & SNAPSHOT_BEGIN:
            self._pending = {}
        target = self._pending if self._pending is not None else self._bars
        bar = chart_bar(self.interval, time_ms, open_, high, low, close, volume, self.full_session)
        changed = False
        if flags & REMOVE:
            # A removed bar's prices may be zeroed; only its time says which one
            slot = chart_bar(self.interval, time_ms, 1, 1, 1, 1, 0, self.full_session)
            changed = slot is not None and target.pop(slot["time"], None) is not None
        elif bar is not None:
            target[bar["time"]] = bar
            changed = True
        if self._pending is not None:
            if flags & (SNAPSHOT_END | SNAPSHOT_SNIP):
                self._bars, self._pending = self._pending, None
                self.ready = True
                return "snapshot"
            return None
        if not self.ready or not changed:
            return None
        if flags & REMOVE:
            return "snapshot"
        self.last_changed = bar
        return "update"

    def bars(self) -> list[dict]:
        return [self._bars[t] for t in sorted(self._bars)]
