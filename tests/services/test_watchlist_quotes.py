"""The chart grid's watchlist quotes (#25)."""

from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.core.quotes import Quote
from optionsjourney.services.watchlist_quotes import WatchlistQuotes, row, snapshot_quotes

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


class FakeSchwab:
    name = "Schwab"

    def quotes(self, symbols):
        assert set(symbols) <= {"$SPX", "AAPL", "/ES"}
        return {
            "AAPL": {
                "assetMainType": "EQUITY",
                "quote": {"lastPrice": 336.7, "mark": 336.1, "closePrice": 336.64},
                "regular": {"regularMarketLastPrice": 336.64, "regularMarketNetChange": -3.78},
            },
            "/ESZ26": {
                "assetMainType": "FUTURE",
                "reference": {"product": "/ES"},
                "quote": {"lastPrice": 7866, "mark": 7866, "closePrice": 7816.25},
            },
        }


class FakeTasty:
    name = "TastyTrade"

    def market_data(self, by_type):
        assert by_type == {"index": ["SPX"], "equity": []}
        return [{"symbol": "SPX", "last": "7811.54", "mark": None, "prev-close": "7765.81"}]


def test_a_row_measures_last_and_mark_from_the_previous_close():
    assert row(110, 100, 111) == {
        "last": 110.0,
        "net_chg": 10.0,
        "net_pct": 10.0,
        "mark": 111.0,
        "mark_pct": 11.0,
    }
    no_mark = row(110, 100)
    assert no_mark["mark"] == 110.0 and no_mark["mark_pct"] == 10.0
    assert row(110, None)["net_chg"] is None


def test_snapshot_uses_schwab_first_and_its_regular_session_change():
    found = snapshot_quotes([FakeTasty(), FakeSchwab()], ["AAPL", "/ES", "SPX"])
    assert found["AAPL"]["last"] == 336.64
    assert round(found["AAPL"]["prev_close"], 2) == 340.42  # the close the change is from
    assert found["/ES"]["prev_close"] == 7816.25  # futures: the settlement
    assert found["SPX"]["prev_close"] == "7765.81"  # what Schwab didn't answer: TastyTrade


class Hub:
    def __init__(self):
        self.subscribed = set()
        self.quotes = {}

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        return self.quotes.get(key)


def test_pushes_only_changed_rows_to_each_client():
    hub, sent = Hub(), []
    quotes = WatchlistQuotes(
        hub, lambda: [FakeSchwab()], lambda *a, **k: sent.append((a, k)), start_thread=False
    )
    quotes.subscribe("s1", ["aapl", "/ES", "bad symbol"])
    assert hub.subscribed == {"AAPL"}  # futures roots come from the snapshot only
    (name, payload), to = sent[0][0], sent[0][1]
    assert name == "watchlist_quotes" and to == {"to": "s1"}
    assert set(payload["quotes"]) == {"AAPL", "/ES"} and payload["source"] == "rest"

    hub.quotes["AAPL"] = Quote("AAPL", Decimal("341"), Decimal("341.2"), Decimal("341.1"), "x", NOW)
    quotes.push()
    payload = sent[-1][0][1]
    assert list(payload["quotes"]) == ["AAPL"]  # /ES didn't change
    assert payload["quotes"]["AAPL"]["last"] == 341.1
    assert payload["source"] == "rest"  # /ES still from the snapshot

    count = len(sent)
    quotes.push()
    assert len(sent) == count  # nothing changed, nothing sent
    quotes.unsubscribe("s1")
    quotes.push()
    assert len(sent) == count
