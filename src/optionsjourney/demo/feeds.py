"""The pretend market's prices and candles, fed into the app's own seams.

The quote hub and the chart feed are the real ones; only their sources are
pretend. Nothing here opens a connection.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal

from optionsjourney.core.black_scholes import greeks
from optionsjourney.core.chart_bars import HISTORY, SNAPSHOT_BEGIN, SNAPSHOT_END
from optionsjourney.core.symbols import parse_occ
from optionsjourney.demo.market import RATE, PretendMarket
from optionsjourney.services.chart_feed import ChartFeed
from optionsjourney.services.streaming import QuoteHub

_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 7 * 1440, "mo": 30 * 1440}
_HISTORY_BARS = 1000
# A long-lived seat's pages can't grow the quote list without end. One SPX expiration is
# about 465 keys (strikes 10% either side of spot, a call and a put each), and a visitor
# can have a few open at once (the 0DTE chain the bots read, a weekly on the Trade page,
# an ETF chain) besides the positions' own: so room for three SPX chains.
MAX_KEYS = 1500


class DemoStream:
    """Stands in for the TastyTrade stream: always up, never connects."""

    name = "tastytrade"
    up = True
    state = "streaming"

    def __init__(self) -> None:
        self._keys: dict[str, None] = {}  # insertion-ordered: oldest subscription first

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    def subscribe(self, keys) -> None:
        """Add keys as the newest, then drop the oldest past MAX_KEYS, but never one of
        these keys: a chain arrives as one set, whose order is only hash order, so
        cutting it would drop arbitrary strikes (a different few in each process)."""
        keys = list(dict.fromkeys(keys))
        for key in keys:
            self._keys.pop(key, None)  # subscribing again makes it the newest
            self._keys[key] = None
        while len(self._keys) > max(MAX_KEYS, len(keys)):
            del self._keys[next(iter(self._keys))]

    def start(self) -> None:
        return None


def _d(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def push_quotes(hub: QuoteHub, stream: DemoStream, market: PretendMarket) -> None:
    for key in sorted(stream.keys):
        try:
            bid, ask, last = market.quote(key)
        except KeyError, ValueError:
            continue  # a ticker the pretend market doesn't have
        hub.update("tastytrade", key, bid=_d(bid), ask=_d(ask), last=_d(last))
        parsed = parse_occ(key)
        if parsed is not None:
            spot = market.last(parsed[0])
            strike, years, vol, put_call = market.option_inputs(key, spot, market.now)
            delta = greeks(spot, strike, years, RATE, vol, put_call)["delta"]
            hub.put_delta(key, _d(delta))


def make_chart_feed(emit: Callable[..., None]) -> ChartFeed:
    return ChartFeed(lambda: None, emit, autostart=False)


def _minutes(interval: str) -> int | None:
    """Minutes in one bar of a chart interval ("5m", "1h", "1mo"), or None."""
    found = re.fullmatch(r"(\d+)(mo|m|h|d|w)", interval)
    return int(found.group(1)) * _MINUTES[found.group(2)] if found else None


def push_candles(feed: ChartFeed, market: PretendMarket) -> None:
    """Every wanted chart gets its history once (a snapshot), then the current bar."""
    for symbol, interval in sorted(feed.wanted()):
        minutes = _minutes(interval)
        if interval not in HISTORY or minutes is None:
            continue
        event_symbol = f"{symbol}{{={interval}}}"
        first = feed.snapshot(symbol, interval) is None
        try:
            bars = market.candles(symbol, minutes, _HISTORY_BARS if first else 1)
        except KeyError:
            continue  # a ticker the pretend market doesn't have
        if first:
            for n, bar in enumerate(bars):
                flags = (SNAPSHOT_BEGIN if n == 0 else 0) | (
                    SNAPSHOT_END if n == len(bars) - 1 else 0
                )
                feed.on_candle(_event(event_symbol, flags, bar))
        else:
            for bar in bars:
                feed.on_candle(_event(event_symbol, 0, bar))


def _event(event_symbol: str, flags: int, bar: dict) -> dict:
    return {"eventSymbol": event_symbol, "eventFlags": flags, **bar}
