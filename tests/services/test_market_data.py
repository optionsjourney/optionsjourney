from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import DailyClose
from optionsjourney.services.market_data import CloseCache
from tests.services.test_expiry_prices import FakeSchwab, FakeTasty

MON = date(2026, 10, 19)
FRI = date(2026, 10, 16)
EVENING = datetime(2026, 10, 19, 22, 30, tzinfo=UTC)
FINAL = {
    "symbol": "SPX",
    "close": "6650.25",
    "close-price-type": "Final",
    "summary-date": "2026-10-19",
}


def test_tastytrade_final_close_answers_without_asking_schwab():
    prev = {"symbol": "SPX", "prev-close": "6600.5", "prev-close-date": "2026-10-16"}
    schwab = FakeSchwab(closes=[DailyClose("SPX", FRI, Decimal("1"))])
    cache = CloseCache(now=lambda: EVENING)
    got = cache.closes([FakeTasty([prev]), schwab], {("SPX", FRI)})
    assert got == {("SPX", FRI): Decimal("6600.5")}
    assert schwab.calls == []


def test_a_final_close_on_the_day_itself_is_kept():
    cache = CloseCache(now=lambda: EVENING)
    got = cache.closes([FakeTasty([FINAL])], {("SPX", MON)})
    assert got == {("SPX", MON): Decimal("6650.25")}


def test_a_close_found_on_a_retry_is_served_from_the_cache_after():
    clock = [EVENING]
    schwab = FakeSchwab(raises=BrokerError("down"))
    cache = CloseCache(now=lambda: clock[0])
    wanted = {("SPX", FRI)}
    assert cache.closes([schwab], wanted) == {}
    assert ("SPX", FRI) in cache._missed
    schwab.raises = None
    schwab.closes = [DailyClose("SPX", FRI, Decimal("6600"))]
    clock[0] = EVENING + timedelta(minutes=6)
    assert cache.closes([schwab], wanted) == {("SPX", FRI): Decimal("6600")}
    assert ("SPX", FRI) not in cache._missed
    assert cache.closes([schwab], wanted) == {("SPX", FRI): Decimal("6600")}
    assert len(schwab.calls) == 2


def test_a_close_no_broker_has_is_asked_again_only_after_five_minutes():
    clock = [EVENING]
    schwab = FakeSchwab(raises=BrokerError("down"))
    cache = CloseCache(now=lambda: clock[0])
    wanted = {("SPX", FRI)}
    assert cache.closes([schwab], wanted) == {}
    clock[0] = EVENING + timedelta(minutes=4)
    assert cache.closes([schwab], wanted) == {}
    assert len(schwab.calls) == 1
    clock[0] = EVENING + timedelta(minutes=6)
    cache.closes([schwab], wanted)
    assert len(schwab.calls) == 2


def test_only_official_closes_are_kept():
    indicative = {**FINAL, "close-price-type": "Indicative"}
    cache = CloseCache(now=lambda: EVENING)
    assert cache.closes([FakeTasty([indicative])], {("SPX", MON)}) == {}


def test_a_tastytrade_error_of_any_kind_falls_to_schwab():
    schwab = FakeSchwab(closes=[DailyClose("SPX", FRI, Decimal("6600"))])
    cache = CloseCache(now=lambda: EVENING)
    got = cache.closes([FakeTasty(raises=ValueError("not json")), schwab], {("SPX", FRI)})
    assert got == {("SPX", FRI): Decimal("6600")}
