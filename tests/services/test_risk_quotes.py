"""Analyze's live leg quotes (#26)."""

from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.core.quotes import Quote
from optionsjourney.services.risk_quotes import RiskQuotes, hub_key

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def test_dxlink_symbols_map_to_hub_keys():
    assert hub_key(".SPXW261017C5800") == "SPXW  261017C05800000"
    assert hub_key("SPX") == "SPX"
    assert hub_key("/ESZ26:XCME") == "dx:/ESZ26:XCME"
    assert hub_key("./E4AU26C7965:XCME") == "dx:./E4AU26C7965:XCME"


class Hub:
    def __init__(self):
        self.subscribed, self.quotes = set(), {}

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        return self.quotes.get(key)


def test_sends_each_page_only_what_changed():
    hub, sent = Hub(), []
    quotes = RiskQuotes(hub, lambda *a, **k: sent.append((a, k)), start_thread=False)
    hub.quotes["SPX"] = Quote("SPX", None, None, Decimal("7811.5"), "tastytrade", NOW)
    quotes.subscribe("s1", ["SPX", ".SPXW261017C5800", "not a symbol"])
    assert hub.subscribed == {"SPX", "SPXW  261017C05800000"}
    (name, payload), to = sent[0]
    assert name == "risk_quotes" and to == {"to": "s1"}
    assert payload["quotes"] == {"SPX": {"bid": None, "ask": None, "last": 7811.5}}
    key = "SPXW  261017C05800000"
    hub.quotes[key] = Quote(key, Decimal("1.2"), Decimal("1.4"), None, "tastytrade", NOW)
    quotes.push()
    assert list(sent[-1][0][1]["quotes"]) == [".SPXW261017C5800"]
    count = len(sent)
    quotes.push()
    assert len(sent) == count
