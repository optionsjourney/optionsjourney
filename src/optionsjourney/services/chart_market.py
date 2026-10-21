"""Market data the chart grid asks for by REST (#25), in Tracker2's answers:

- price history: Schwab's (as Tracker2), TastyTrade's DXLink history when
  Schwab isn't connected. The pages use it when the live feed sends nothing.
- a futures root's active contract ("/ES" -> "/ESZ26:XCME"), from TastyTrade.
- a futures root's previous settlement, Schwab first, then TastyTrade."""

import logging
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.chart_bars import chart_bar
from optionsjourney.core.market import MARKET_TZ, market_date

logger = logging.getLogger(__name__)

HISTORY_SYMBOL = re.compile(r"^\$?[A-Z0-9./]{1,20}$")
FUTURES_ROOT = re.compile(r"^/[A-Z0-9]{1,6}$")
CONTRACT_FRESH_FOR = 3600  # seconds
_FREQUENCY_INTERVAL = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 1440: "1d"}
_UNAVAILABLE = (BrokerError, CredentialsUnavailable, OSError, TimeoutError)


class MarketError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def history_end(now_et: datetime) -> datetime:
    """Now during or after the session; Friday 16:00 at the weekend; the day
    before's 16:00 on a weekday before 09:30."""
    weekday = now_et.weekday()
    before_open = (now_et.hour, now_et.minute) < (9, 30)
    if weekday >= 5:
        back = weekday - 4
    elif before_open:
        back = 1
    else:
        return now_et
    return now_et.replace(hour=16, minute=0, second=0, microsecond=0) - timedelta(days=back)


def _hourly(bars: list[dict]) -> list[dict]:
    """Half-hour chart bars rolled up into hours starting 09:30, 10:30 ..."""
    out: list[dict] = []
    for bar in bars:
        day = bar["time"] - bar["time"] % 86400
        start = day + 570 * 60 + ((bar["time"] - day - 570 * 60) // 3600) * 3600
        if out and out[-1]["time"] == start:
            last = out[-1]
            last["high"] = max(last["high"], bar["high"])
            last["low"] = min(last["low"], bar["low"])
            last["close"] = bar["close"]
            last["volume"] += bar["volume"]
        else:
            out.append(dict(bar, time=start))
    return out


def _daily_bar(when: datetime, o, h, lo, c, v) -> dict | None:
    """A daily bar at 00:00 UTC of its session date, as the live feed sends."""
    day = market_date(when) if when.astimezone(UTC).hour else when.astimezone(UTC).date()
    stamp = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    return chart_bar("1d", stamp, o, h, lo, c, v)


def schwab_history(client, symbol: str, frequency: int, period: int, now: datetime) -> list[dict]:
    now_et = now.astimezone(MARKET_TZ)
    end = history_end(now_et)
    if frequency >= 1440:
        start = end - timedelta(days=period)
        params = {"periodType": "month", "frequencyType": "daily", "frequency": 1}
    else:
        start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=period - 1)
        fetched = 30 if frequency == 60 else frequency
        params = {"frequencyType": "minute", "frequency": fetched}
    params |= {
        "startDate": int(start.timestamp() * 1000),
        "endDate": int(end.timestamp() * 1000),
        "needExtendedHoursData": "false",
    }
    raw = client.price_history(symbol, params)
    bars = []
    for c in raw:
        values = (c.get("open"), c.get("high"), c.get("low"), c.get("close"), c.get("volume"))
        if frequency >= 1440:
            bar = _daily_bar(datetime.fromtimestamp(c["datetime"] / 1000, UTC), *values)
        else:
            bar = chart_bar("5m", c["datetime"], *values)
        if bar is not None:
            bars.append(bar)
    bars.sort(key=lambda b: b["time"])
    return _hourly(bars) if frequency == 60 else bars


def tastytrade_history(client, symbol: str, frequency: int, period: int, now: datetime):
    from optionsjourney.services.candles import tastytrade_candles  # noqa: PLC0415

    interval = _FREQUENCY_INTERVAL.get(frequency)
    if interval is None:
        return []
    bars = []
    for c in tastytrade_candles(client, symbol.lstrip("$"), interval, now):
        values = (c.open, c.high, c.low, c.close, c.volume)
        if interval == "1d":
            bar = _daily_bar(c.time, *values)
        else:
            bar = chart_bar(interval, int(c.time.timestamp() * 1000), *values)
        if bar is not None:
            bars.append(bar)
    start = (now - timedelta(days=period)).timestamp()
    return [b for b in bars if b["time"] >= start]


def price_history(clients: list, symbol: str, frequency: int, period: int, now=None) -> dict:
    symbol = (symbol or "").strip().upper()
    if not HISTORY_SYMBOL.match(symbol):
        raise MarketError("bad symbol")
    now = now or datetime.now(UTC)
    attempts = [(c, schwab_history) for c in clients if hasattr(c, "price_history")]
    attempts += [(c, tastytrade_history) for c in clients if hasattr(c, "quote_token")]
    errors = []
    for client, get in attempts:
        try:
            candles = get(client, symbol, frequency, period, now)
        except _UNAVAILABLE as e:
            errors.append(str(e))
            continue
        if candles:
            return {"candles": candles, "symbol": symbol}
    if errors:
        raise MarketError("; ".join(errors), 502)
    return {"candles": [], "symbol": symbol}


class FuturesContracts:
    """A futures root's active contract, kept an hour."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, str]] = {}

    def front(self, clients: list, root: str) -> str | None:
        root = (root or "").strip().upper()
        if not FUTURES_ROOT.match(root):
            return None
        with self._lock:
            cached = self._items.get(root)
        if cached and time.monotonic() - cached[0] < CONTRACT_FRESH_FOR:
            return cached[1]
        for client in clients:
            if not hasattr(client, "futures"):
                continue
            try:
                contracts = client.futures(root.lstrip("/"))
            except _UNAVAILABLE as e:
                logger.info("no %s contracts: %s", root, e)
                continue
            active = [c for c in contracts if c.get("active-month") and c.get("streamer-symbol")]
            if active:
                with self._lock:
                    self._items[root] = (time.monotonic(), active[0]["streamer-symbol"])
                return active[0]["streamer-symbol"]
        return None


def futures_prev_close(clients: list, root: str) -> Decimal:
    """The prior settlement the day's change is measured from."""
    root = (root or "").strip().upper()
    if not FUTURES_ROOT.match(root):
        raise MarketError("futures root only")
    errors = []
    for client in clients:
        try:
            if hasattr(client, "quotes"):
                # "/ES" comes back keyed by its front month ("/ESZ26"), the root as its product
                for key, entry in (client.quotes([root]) or {}).items():
                    entry = entry or {}
                    product = ((entry.get("reference") or {}).get("product") or "").upper()
                    close = (entry.get("quote") or {}).get("closePrice")
                    if root in (key.upper(), product) and close:
                        return Decimal(str(close))
            elif hasattr(client, "market_data") and hasattr(client, "futures"):
                contracts = client.futures(root.lstrip("/"))
                active = [c["symbol"] for c in contracts if c.get("active-month")]
                for item in client.market_data({"future": active[:1]}) if active else []:
                    if item.get("prev-close"):
                        return Decimal(str(item["prev-close"]))
        except _UNAVAILABLE as e:
            errors.append(str(e))
    if errors:
        raise MarketError("; ".join(errors), 502)
    raise MarketError(f"no settle for {root}", 404)
