from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.app import create_app
from optionsjourney.core.quotes import Quote

AT = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


class FakeHub:
    def __init__(self):
        self.subscribed = set()

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        if key == "SPY":
            return Quote(
                "SPY", Decimal("778.5"), Decimal("778.6"), Decimal("778.55"), "tastytrade", AT
            )
        return None

    def status(self):
        return {"tastytrade": "streaming", "schwab": "not connected"}


def test_quotes_api(settings, fake_credentials):
    hub = FakeHub()
    app = create_app(
        settings, credentials=fake_credentials, broker_clients=lambda: [], quote_hub=hub
    )
    body = app.test_client().get("/api/quotes?symbols=SPY,XYZ").get_json()
    assert hub.subscribed == {"SPY", "XYZ"}
    assert body["quotes"]["SPY"] == {
        "bid": 778.5,
        "ask": 778.6,
        "last": 778.55,
        "mark": 778.55,
        "source": "tastytrade",
        "at": AT.isoformat(),
    }
    assert body["quotes"]["XYZ"] is None
    assert body["streams"] == {"tastytrade": "streaming", "schwab": "not connected"}


def test_option_symbols_keep_their_spaces(settings, fake_credentials):
    hub = FakeHub()
    app = create_app(
        settings, credentials=fake_credentials, broker_clients=lambda: [], quote_hub=hub
    )
    app.test_client().get("/api/quotes", query_string={"symbols": "SPXW  261016P05800000"})
    assert hub.subscribed == {"SPXW  261016P05800000"}


def test_settings_shows_live_price_status(settings, fake_credentials):
    app = create_app(
        settings, credentials=fake_credentials, broker_clients=lambda: [], quote_hub=FakeHub()
    )
    html = app.test_client().get("/settings").get_data(as_text=True)
    assert "Live prices" in html and "TastyTrade · streaming" in html
