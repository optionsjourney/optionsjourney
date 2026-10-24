import re
from datetime import UTC, datetime

import pytest

from optionsjourney.app import create_app
from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.transactions import RawTransaction

ORIGIN = {"Origin": "http://localhost"}

SELL_TO_OPEN = {
    "id": 401234567,
    "order-id": 9001,
    "symbol": "SPXW  261009P05800000",
    "instrument-type": "Equity Option",
    "underlying-symbol": "SPXW",
    "transaction-type": "Trade",
    "action": "Sell to Open",
    "quantity": "1.0",
    "price": "2.5",
    "value": "250.0",
    "value-effect": "Credit",
    "commission": "1.0",
    "commission-effect": "Debit",
}


class FakeTastyTrade:
    name = "TastyTrade"
    broker = "tastytrade"

    def __init__(self, executed_at):
        self.executed_at = executed_at

    def accounts(self):
        return [BrokerAccount("tastytrade", "5WX01234", "Alpha")]

    def balance(self, number):
        raise AssertionError("not used")

    def transactions(self, number, start, end):
        data = dict(SELL_TO_OPEN, **{"executed-at": self.executed_at.isoformat()})
        return [RawTransaction("401234567", self.executed_at, "Trade", data)]


@pytest.fixture
def client(settings, fake_credentials):
    broker = FakeTastyTrade(datetime.now(UTC))
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    return app.test_client()


def test_empty_before_first_sync(client):
    html = client.get("/transactions").get_data(as_text=True)
    assert "Nothing synced yet" in html


def test_sync_then_list(client):
    response = client.post("/sync", headers=ORIGIN)
    assert response.status_code == 302

    html = client.get("/transactions").get_data(as_text=True)
    assert "TastyTrade: 1 accounts, 1 new, 0 updated." in html
    assert "SPX Short Put" in html
    assert "-1 SPX 10/09/26 5800 Put" in html
    assert "1 orders from 1 fills" in html
    assert "Order 9001" in html  # the broker's order id, which groups the fills
    assert "$250.00" in html
    assert "Alpha" in html and "••1234" in html


def test_fills_view(client):
    client.post("/sync", headers=ORIGIN)
    html = client.get("/transactions?view=fills").get_data(as_text=True)
    assert "SPX 10/09/26 5800 Put" in html
    assert "Sell to Open" in html
    assert "1 fills" in html
    assert "401234567" in html  # the broker's transaction id


def test_account_filter(client):
    client.post("/sync", headers=ORIGIN)
    assert "5800 Put" in client.get("/transactions?account=tastytrade:5WX01234").get_data(
        as_text=True
    )
    assert "5800 Put" not in client.get("/transactions?account=schwab:999").get_data(as_text=True)


def test_post_from_another_site_is_refused(client):
    assert client.post("/sync").status_code == 403
    assert client.post("/sync", headers={"Origin": "https://evil.example"}).status_code == 403


def test_the_columns_and_the_view_choice_have_tips(client):
    client.post("/sync", headers=ORIGIN)
    orders = client.get("/transactions").get_data(as_text=True)
    for tip in ("transactions-show", "transactions-value-orders", "transactions-broker-id-orders"):
        assert f'id="tip-{tip}"' in orders
    fills = client.get("/transactions?view=fills").get_data(as_text=True)
    for tip in ("transactions-instrument", "transactions-value-fills",
                "transactions-broker-id-fills"):  # fmt: skip
        assert f'id="tip-{tip}"' in fills
    # The tip sits beside the Show label, never inside it (a click on the label would open it)
    assert not re.search(r"<label>\s*Show\s*<select[^>]*>.*?tip-transactions-show.*?</label>",
                         orders, re.S)  # fmt: skip


def test_search_filters_orders_and_fills(client):
    client.post("/sync", headers=ORIGIN)
    for view in ("orders", "fills"):
        html = client.get(f"/transactions?view={view}&q=spx+put").get_data(as_text=True)
        assert "SPX 10/09/26 5800 Put" in html
        html = client.get(f"/transactions?view={view}&q=AAPL").get_data(as_text=True)
        assert "Nothing matches “AAPL” in this period." in html
        assert "5800 Put" not in html


def test_search_finds_ids_and_account_names(client):
    client.post("/sync", headers=ORIGIN)
    for q in ("9001", "401234567", "alpha", "sell to open"):
        html = client.get(f"/transactions?view=fills&q={q}").get_data(as_text=True)
        assert "SPX 10/09/26 5800 Put" in html, q
