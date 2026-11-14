from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.services import chain
from tests.services.bot_fakes import FakeHub

AT = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)


def test_a_hub_quote_carries_its_time_for_the_bots_freshness_check():
    hub = FakeHub()
    hub.set("SPXW  261013P05700000", 1.0, 1.2, AT)
    found = chain._side_quote(hub, "SPXW  261013P05700000", symbol="S")
    assert found == {"bid": 1.0, "ask": 1.2, "at": AT, "symbol": "S"}
    assert chain._side_quote(hub, "nothing") is None


def test_a_hub_quote_carries_the_streams_greeks_with_their_time():
    hub = FakeHub()
    hub.set("K", 1.0, 1.2, AT)
    hub.set_delta("K", "-0.21", AT)
    found = chain._side_quote(hub, "K", symbol="S")
    assert (found["stream_delta"], found["stream_delta_at"]) == (Decimal("-0.21"), AT)


# --- #327: expirations, each broker's own answer and where an entry's came from ---------

from datetime import date  # noqa: E402

from optionsjourney.bots.market_check import Listing  # noqa: E402
from optionsjourney.brokers.http import BrokerError  # noqa: E402

AHEAD = (date(2030, 1, 18), date(2030, 1, 25))  # always after the real today


class Tasty:
    def __init__(self, days=AHEAD, error=None):
        self.days, self.error, self.asked = days, error, 0

    def quote_token(self):
        return "t", "wss://x"

    def option_chain(self, symbol):
        self.asked += 1
        if self.error is not None:
            raise self.error
        root = "SPXW" if symbol == "SPX" else symbol
        return [{"root-symbol": "SPX", "expirations": [{"expiration-date": "2030-02-15"}]},
                {"root-symbol": root, "expirations": [
                    {"expiration-date": d.isoformat()} for d in self.days]}]  # fmt: skip


class Schwab:
    def __init__(self, days=AHEAD, error=None):
        self.days, self.error = days, error

    def option_expirations(self, symbol):
        if self.error is not None:
            raise self.error
        return list(self.days)


def test_expirations_from_tastytrade_say_nothing_of_a_backup():
    found = chain.ChainData().expirations([Tasty(), Schwab()], "SPX")
    assert found == {"expirations": ["2030-01-18", "2030-01-25"]}


def test_expirations_from_schwab_when_tastytrade_fails_say_so():
    # Tracker2's 2026-11-09 outage: a response TastyTrade's side couldn't read
    for error in (BrokerError("down"), KeyError("tick-sizes")):
        found = chain.ChainData().expirations([Tasty(error=error), Schwab()], "SPX")
        assert found == {"expirations": ["2030-01-18", "2030-01-25"], "backup": "Schwab"}
    found = chain.ChainData().expirations([Tasty(days=()), Schwab()], "SPX")
    assert found["backup"] == "Schwab"


def test_expirations_from_schwab_alone_are_no_backup():
    # A friend with only Schwab connected: Schwab is the source, not a stand-in
    assert chain.ChainData().expirations([Schwab()], "SPX") == {
        "expirations": ["2030-01-18", "2030-01-25"]
    }


def test_the_backup_note_is_kept_with_the_cached_dates():
    data, tasty = chain.ChainData(), Tasty(error=BrokerError("down"))
    data.expirations([tasty, Schwab()], "SPX")
    again = data.expirations([tasty, Schwab()], "SPX")
    assert again["backup"] == "Schwab" and tasty.asked == 1


def test_broker_expirations_asks_each_broker_fresh_and_never_raises():
    data, tasty = chain.ChainData(), Tasty()
    data.expirations([tasty], "SPX")  # cached for the bots
    found = data.broker_expirations([tasty, Schwab(error=BrokerError("Schwab timed out"))], "SPX")
    assert tasty.asked == 2  # asked again, not the cache
    assert found["tastytrade"] == Listing(True, AHEAD)
    assert found["schwab"] == Listing(True, (), "Schwab timed out")


def test_broker_expirations_names_an_error_that_has_no_message():
    found = chain.ChainData().broker_expirations([Tasty(error=KeyError())], "SPX")
    assert found["tastytrade"] == Listing(True, (), "KeyError")


def test_broker_expirations_a_broker_not_connected():
    found = chain.ChainData().broker_expirations([Tasty()], "SPX")
    assert found["schwab"] == Listing(False)
    # The demo's pretend Schwab has no expiration list: it counts as not connected
    from optionsjourney.demo.broker import SCHWAB_ACCOUNT, Book, PretendSchwab
    from optionsjourney.demo.market import PretendMarket

    market = PretendMarket(seed=1, start=datetime(2026, 10, 21, 13, 40, tzinfo=UTC))
    pretend = PretendSchwab(market, Book(SCHWAB_ACCOUNT))
    assert chain.ChainData().broker_expirations([pretend], "SPX")["schwab"] == Listing(False)


def test_option_rows_from_says_which_broker_answered():
    class SchwabChain(Schwab):
        def option_chain(self, symbol, expiration, count):
            raise BrokerError("down")

    class TastyRows(Tasty):
        def option_chain(self, symbol):
            return [{"root-symbol": "SPXW", "expirations": [{
                "expiration-date": "2030-01-18",
                "strikes": [{"strike-price": "5800.0", "call": "C", "put": "P"}]}]}]  # fmt: skip

    hub = FakeHub()
    hub.set("C", 1.0, 1.2, AT)
    hub.set("P", 1.0, 1.2, AT)
    hub.set_last("SPX", 5800, AT)
    rows, source = chain.ChainData().option_rows_from(
        [TastyRows(), SchwabChain()], hub, "SPX", "2030-01-18"
    )
    assert source == "tastytrade" and rows[5800.0]["put"]["symbol"] == "P"
    assert chain.ChainData().option_rows_from([], hub, "SPX", "2030-01-18") == (None, None)
