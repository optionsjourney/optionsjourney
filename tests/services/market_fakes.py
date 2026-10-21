"""A fake DXLink feed for the market filter tests: it answers each read from
what the test put in it, and records what was asked."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from optionsjourney.core.market import MARKET_TZ
from optionsjourney.services.market_feed import SNAPSHOT_END, Got

D = Decimal


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def et(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), MARKET_TZ)


def day_id(day: date) -> int:
    return (day - date(1970, 1, 1)).days


class FakeFeed:
    def __init__(self):
        self.trades: dict[str, tuple[Decimal, int]] = {}
        self.summaries: dict[str, tuple[int, Decimal | None]] = {}
        self.candles: dict[str, list[tuple[int, Decimal, Decimal]]] = {}  # (ms, open, close)
        self.incomplete: set[str] = set()  # candle symbols whose snapshot never ends
        self.calls: list = []
        self.readies: list = []  # each read's "have I got what I need?" check
        self.on_read = None  # called on every read (a test can move the clock)
        self.error: Exception | None = None
        self.source = "TastyTrade"  # the broker each answer names

    def trade(self, symbol, price, at: datetime):
        self.trades[symbol] = (D(str(price)), ms(at))

    def bars(self, symbol, rows):
        """rows: (datetime, open, close)."""
        self.candles[symbol] = [(ms(at), D(str(o)), D(str(c))) for at, o, c in rows]

    def read(self, want, wait_s, ready):
        self.calls.append((want, wait_s))
        self.readies.append(ready)
        if self.on_read is not None:
            self.on_read()
        if self.error is not None:
            raise self.error
        got = Got(source=self.source)
        for symbol in want.trades:
            if symbol in self.trades:
                price, at = self.trades[symbol]
                got.add_event({"eventType": "Trade", "eventSymbol": symbol, "price": price,
                               "time": at})  # fmt: skip
        for symbol in want.summaries:
            if symbol in self.summaries:
                day, close = self.summaries[symbol]
                got.add_event({"eventType": "Summary", "eventSymbol": symbol, "dayId": day,
                               "prevDayClosePrice": close})  # fmt: skip
        for symbol, from_ms in want.candles:
            for at, open_, close in self.candles.get(symbol, []):
                if at >= from_ms:
                    got.add_event({"eventType": "Candle", "eventSymbol": symbol, "eventFlags": 0,
                                   "time": at, "open": open_, "high": open_, "low": open_,
                                   "close": close, "volume": 1})  # fmt: skip
            if symbol not in self.incomplete:
                got.add_event({"eventType": "Candle", "eventSymbol": symbol,
                               "eventFlags": SNAPSHOT_END | 0x02, "time": 0, "open": 0,
                               "high": 0, "low": 0, "close": 0, "volume": 0})  # fmt: skip
        return got


def daily_history(day: date, closes: list) -> list:
    """Daily bars for the sessions before `day`, newest close first in `closes`,
    each at midnight UTC of a weekday; plus today's forming bar."""
    from datetime import UTC

    rows, d = [], day
    rows.append((datetime.combine(day, time(0), UTC), D("1"), D("999999")))  # today's: ignored
    for close in closes:
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        rows.append((datetime.combine(d, time(0), UTC), D(str(close)), D(str(close))))
    return rows
