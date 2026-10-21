"""The risk graph (#91): Tracker2's retired risk graph for journal trades."""

import pytest
from tests.web.test_journal_pages import KEY, ORIGIN, FakeTastyTrade

from optionsjourney.app import create_app


class Hub:
    def subscribe(self, keys):
        pass

    def latest(self, key):
        return None

    def status(self):
        return {"tastytrade": "streaming"}


@pytest.fixture
def client(settings, fake_credentials):
    broker = FakeTastyTrade()
    app = create_app(
        settings, credentials=fake_credentials, broker_clients=lambda: [broker], quote_hub=Hub()
    )
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)
    return client


def test_page_loads_tracker2s_risk_graph(client):
    html = client.get(f"/risk-graph?trade={KEY}").get_data(as_text=True)
    assert "js/modeler/pricing.js" in html and "js/risk_graph.js" in html
    assert 'id="pl-chart-canvas"' in html and "risk_graph_page.js" in html
    assert "Open in Analyzer" not in html
    embedded = client.get(f"/risk-graph?trade={KEY}&embed=1").get_data(as_text=True)
    assert "nav__brand" not in embedded


def test_api_returns_the_trades_legs(client):
    body = client.get(f"/api/risk-graph?trade={KEY}").get_json()
    assert body["status"] == "success"
    [trade] = body["trades"]
    assert trade["key"] == KEY and trade["symbol"] == "SPX" and len(trade["legs"]) == 4
    short_put = next(leg for leg in trade["legs"] if leg["strike"] == 5800.0)
    assert short_put["side"] == "SHORT" and short_put["entry_price"] == 2.0


def test_api_unknown_and_empty(client):
    assert client.get("/api/risk-graph").status_code == 400
    assert client.get("/api/risk-graph?trade=").status_code == 400
    missing = client.get("/api/risk-graph?trade=nope")
    assert missing.status_code == 404
    assert missing.get_json()["message"] == "That trade isn't in the journal"


def test_api_too_many(client):
    keys = ",".join(f"k{i}" for i in range(51))
    assert client.get(f"/api/risk-graph?trade={keys}").status_code == 400


@pytest.mark.parametrize(
    "tip_id",
    [
        "risk-delta",
        "risk-gamma",
        "risk-theta",
        "risk-vega",
        "risk-greeks-toggle",
        "risk-cost-basis",
        "risk-sl-ratio",
        "risk-at-expiry",
        "risk-theoretical",
    ],
)
def test_risk_graph_labels_have_tips(client, tip_id):
    html = client.get(f"/risk-graph?trade={KEY}").get_data(as_text=True)
    assert f'id="tip-{tip_id}"' in html
    assert 'title="Show position Greeks' not in html
