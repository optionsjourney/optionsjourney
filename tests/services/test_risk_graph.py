from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from optionsjourney.core.positions import Position, PositionLeg
from optionsjourney.core.quotes import Quote
from optionsjourney.core.transactions import InstrumentType, OptionDetails
from optionsjourney.services.risk_graph import trade_graphs

D = Decimal
SHORT_PUT = "SPXW  261120P05800000"


def _entry(key, remaining=True, awaiting=False):
    leg = PositionLeg(
        SHORT_PUT, "SPXW", InstrumentType.OPTION,
        OptionDetails("PUT", D("5800"), date(2026, 11, 20)), -1, D("1"), D("200"),
        closed=D("0") if remaining else D("1"),
    )  # fmt: skip
    position = Position(
        key=key, broker="tastytrade", account_number="5WX01234",
        opened_at=datetime(2026, 10, 1, tzinfo=UTC), legs=[leg], units=D("1"),
        entry_price=D("2"),
    )  # fmt: skip
    return SimpleNamespace(position=position, name="SPX Short Put", awaiting_broker=awaiting)


class Hub:
    def __init__(self):
        self.subscribed = set()

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        if key != SHORT_PUT:
            return None
        return Quote(key, D("1.00"), D("1.20"), None, "tastytrade", datetime.now(UTC))


class Chain:
    def __init__(self, spot=5810.0, fail=False):
        self.spot, self.fail = spot, fail

    def underlying_last(self, clients, hub, ticker):
        if self.fail:
            raise RuntimeError("broker down")
        assert ticker == "SPX"
        return self.spot


def test_a_trade_with_marks_and_spot():
    view = SimpleNamespace(entries=[_entry("k1")])
    hub = Hub()
    [item] = trade_graphs(view, ["k1"], hub, Chain(), [])
    assert hub.subscribed == {SHORT_PUT}
    assert item["key"] == "k1" and item["label"] == "SPX Short Put" and item["symbol"] == "SPX"
    assert item["spot"] == 5810.0 and item["realized_pl"] == 0.0
    assert item["legs"][0]["live_price"] == 1.1


def test_unknown_key_raises():
    with pytest.raises(LookupError):
        trade_graphs(SimpleNamespace(entries=[]), ["nope"], Hub(), Chain(), [])


def test_trade_with_nothing_open_is_skipped_with_a_reason():
    view = SimpleNamespace(entries=[_entry("k1", remaining=False)])
    [item] = trade_graphs(view, ["k1"], Hub(), Chain(), [])
    assert item == {"key": "k1", "error": "SPX Short Put has no open options to graph."}


def test_spot_failure_still_graphs():
    view = SimpleNamespace(entries=[_entry("k1")])
    [item] = trade_graphs(view, ["k1"], Hub(), Chain(fail=True), [])
    assert item["spot"] is None and item["legs"]


class FuturesChain:
    def __init__(self):
        self.asked = []

    def underlying_last(self, clients, hub, ticker):
        raise AssertionError("a futures trade is priced off its own contract, not the root's front")

    def futures_last(self, clients, hub, contract):
        self.asked.append(contract)
        return {"/ESH7": 7910.0}.get(contract)


def test_futures_trade_is_priced_off_its_own_contract():
    symbol = "./ESH7 E1AH7 270319P7000"
    leg = PositionLeg(
        symbol, "/ESH7", InstrumentType.FUTURE_OPTION,
        OptionDetails("PUT", D("7000"), date(2027, 3, 19)), -1, D("1"), D("2275"),
    )  # fmt: skip
    position = Position(
        key="k1", broker="tastytrade", account_number="5WX01234",
        opened_at=datetime(2026, 10, 1, tzinfo=UTC), legs=[leg], units=D("1"),
        entry_price=D("45.5"),
    )  # fmt: skip
    view = SimpleNamespace(
        entries=[SimpleNamespace(position=position, name="/ESH7 Short Put", awaiting_broker=False)]
    )
    chain = FuturesChain()
    [item] = trade_graphs(view, ["k1"], Hub(), chain, [])
    assert item["symbol"] == "/ESH7" and item["spot"] == 7910.0 and chain.asked == ["/ESH7"]
