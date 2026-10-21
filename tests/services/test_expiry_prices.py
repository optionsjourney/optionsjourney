from datetime import UTC, date, datetime
from decimal import Decimal

from optionsjourney.core.accounts import DailyClose
from optionsjourney.services.expiry_prices import official_closes, prelim_prices

DAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 16, 20, 20, tzinfo=UTC)  # 4:20 PM New York, same day
TT_LAST = {"symbol": "SPX", "last": "5000.5", "updated-at": "2026-10-16T20:16:00+00:00"}
# 2026-10-16 20:16 UTC in ms
MS = int(datetime(2026, 10, 16, 20, 16, tzinfo=UTC).timestamp() * 1000)
SCHWAB_QUOTE = {"$SPX": {"quote": {"lastPrice": 5001.5, "tradeTime": MS}}}


class FakeTasty:
    name = "tastytrade"

    def __init__(self, items=(), raises=None):
        self.items, self.raises = list(items), raises

    def market_data(self, by_type):
        if self.raises:
            raise self.raises
        return self.items


class FakeSchwab:
    name = "schwab"

    def __init__(self, closes=(), quotes=None, raises=None, on_call=None):
        self.closes, self._quotes, self.raises = list(closes), quotes or {}, raises
        self.calls: list[str] = []
        self.on_call = on_call

    def daily_closes(self, symbol, start, end):
        self.calls.append(symbol)
        if self.on_call:
            self.on_call()
        if self.raises:
            raise self.raises
        return self.closes

    def quotes(self, symbols):
        if self.raises:
            raise self.raises
        return self._quotes


WANTED = {("SPX", DAY)}


def test_tastytrade_only_gives_an_index_prelim():
    got = prelim_prices([FakeTasty([TT_LAST])], None, WANTED, now=NOW)
    assert got[("SPX", DAY)].source == "tastytrade last"
    assert not got[("SPX", DAY)].final


def test_schwab_only_gives_the_official_close():
    later = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
    schwab = FakeSchwab(closes=[DailyClose("SPX", DAY, Decimal("5002"))])
    got = prelim_prices([schwab], None, WANTED, now=later)
    assert got[("SPX", DAY)].source == "schwab daily close"
    assert got[("SPX", DAY)].final
    assert official_closes([schwab], WANTED, now=later)[("SPX", DAY)].price == Decimal("5002")
    assert official_closes([schwab], WANTED, now=NOW) == {}


def test_a_broker_raising_anything_falls_to_the_other():
    got = prelim_prices(
        [FakeTasty(raises=ValueError("not json")), FakeSchwab(quotes=SCHWAB_QUOTE)],
        None,
        WANTED,
        now=NOW,
    )
    assert got[("SPX", DAY)].source == "schwab last"
    got = prelim_prices(
        [FakeTasty([TT_LAST]), FakeSchwab(raises=KeyError("x"))], None, WANTED, now=NOW
    )
    assert got[("SPX", DAY)].source == "tastytrade last"


def test_nothing_from_either_is_left_out():
    clients = [FakeTasty(raises=ValueError("a")), FakeSchwab(raises=KeyError("b"))]
    assert prelim_prices(clients, None, WANTED, now=NOW) == {}
    assert official_closes(clients, WANTED, now=NOW) == {}


def test_a_slow_schwab_is_asked_once_then_left_for_the_next_try():
    ticks = [0.0]

    def slow():
        ticks[0] += 4.0

    schwab = FakeSchwab(closes=[], on_call=slow)
    wanted = {("SPX", DAY), ("NDX", DAY), ("RUT", DAY)}
    later = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
    got = official_closes([schwab], wanted, now=later, budget_s=3.0, clock=lambda: ticks[0])
    assert got == {}
    assert len(schwab.calls) == 1


def test_tastytrade_first_asks_schwab_only_for_what_tastytrade_did_not_price():
    later = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
    prev = {"symbol": "SPX", "prev-close": "5000", "prev-close-date": "2026-10-16"}
    other = [DailyClose("SPX", DAY, Decimal("4999"))]
    schwab = FakeSchwab(closes=other)
    got = official_closes([FakeTasty([prev]), schwab], WANTED, now=later, tastytrade_first=True)
    assert got[("SPX", DAY)].price == Decimal("5000")
    assert schwab.calls == []
    schwab = FakeSchwab(closes=other)
    official_closes([FakeTasty([prev]), schwab], WANTED, now=later)
    assert schwab.calls == ["SPX"]  # the default still asks Schwab (the 4:15 path)
