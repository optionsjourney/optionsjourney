"""Entering trades by hand (#9)."""

from datetime import date, timedelta
from urllib.parse import quote, unquote

import pytest

from optionsjourney.app import create_app

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def client(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app.test_client()


def _open_spread(client, **extra):
    data = {
        "account": "new",
        "new_account": "Fidelity",
        "date": "2026-09-01",
        "time": "10:00",
        "effect": "open",
        "fees": "1.30",
        "leg-1-type": "put",
        "leg-1-side": "sell",
        "leg-1-quantity": "1",
        "leg-1-underlying": "SPX",
        "leg-1-strike": "5800",
        "leg-1-expiration": "2026-10-16",
        "leg-1-price": "2.00",
        "leg-2-type": "put",
        "leg-2-side": "buy",
        "leg-2-quantity": "1",
        "leg-2-underlying": "SPX",
        "leg-2-strike": "5790",
        "leg-2-expiration": "2026-10-16",
        "leg-2-price": "1.00",
    }
    data.update(extra)
    return client.post("/journal/manual/new", data=data, headers=ORIGIN)


def test_the_journal_links_to_adding_a_trade(client):
    html = client.get("/journal").get_data(as_text=True)
    assert 'href="/journal/manual/new"' in html


def test_the_form(client):
    html = client.get("/journal/manual/new").get_data(as_text=True)
    assert "Add a trade" in html and "leg-4-strike" in html


def test_adding_a_trade(client):
    response = _open_spread(client)
    assert response.status_code == 302
    key = unquote(response.location.split("/journal/trades/")[1].split("?")[0])
    assert key.startswith("manual:M001:")
    html = client.get("/journal").get_data(as_text=True)
    assert "SPX Put Credit Spread" in html and "Fidelity" in html
    trade = client.get(response.location).get_data(as_text=True)
    assert "Entered by hand" in trade and "Close" in trade and "Delete" in trade


def test_a_bad_entry_shows_the_form_again_with_the_reason(client):
    response = _open_spread(client, **{"leg-1-strike": ""})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Leg 1: strike must be a number." in html
    assert 'value="5790"' in html  # what was typed is kept


def test_closing_a_manual_trade(client):
    key = unquote(_open_spread(client).location.split("/journal/trades/")[1].split("?")[0])
    form = client.get("/journal/manual/new?close=" + quote(key, safe="")).get_data(as_text=True)
    assert 'value="close" checked' in form and 'value="5800"' in form
    client.post(
        "/journal/manual/new",
        data={
            "account": "M001",
            "date": "2026-09-02",
            "time": "10:00",
            "effect": "close",
            "fees": "0",
            "leg-1-type": "put",
            "leg-1-side": "buy",
            "leg-1-quantity": "1",
            "leg-1-underlying": "SPX",
            "leg-1-strike": "5800",
            "leg-1-expiration": "2026-10-16",
            "leg-1-price": "0.50",
            "leg-2-type": "put",
            "leg-2-side": "sell",
            "leg-2-quantity": "1",
            "leg-2-underlying": "SPX",
            "leg-2-strike": "5790",
            "leg-2-expiration": "2026-10-16",
            "leg-2-price": "0.10",
        },
        headers=ORIGIN,
    )
    html = client.get("/journal?status=closed").get_data(as_text=True)
    assert "SPX Put Credit Spread" in html


def test_marking_a_manual_trade_expired(client):
    expired = {"leg-1-expiration": "2026-09-04", "leg-2-expiration": "2026-09-04"}
    key = unquote(
        _open_spread(client, **expired).location.split("/journal/trades/")[1].split("?")[0]
    )
    client.post("/journal/manual/expire", data={"key": key}, headers=ORIGIN)
    html = client.get("/journal/trades/" + quote(key, safe=":")).get_data(as_text=True)
    assert "Closed · Expired" in html


def test_a_trade_cant_expire_before_its_expiration(client):
    later = (date.today() + timedelta(days=60)).isoformat()
    opened = _open_spread(client, **{"leg-1-expiration": later, "leg-2-expiration": later})
    key = unquote(opened.location.split("/journal/trades/")[1].split("?")[0])
    response = client.post(
        "/journal/manual/expire", data={"key": key}, headers=ORIGIN, follow_redirects=True
    )
    assert "expired yet" in response.get_data(as_text=True)


def test_deleting_a_manual_entry(client):
    key = unquote(_open_spread(client).location.split("/journal/trades/")[1].split("?")[0])
    entry_id = key.rsplit(":", 1)[1]
    client.post(f"/journal/manual/entries/{entry_id}/delete", headers=ORIGIN)
    assert "SPX Put Credit Spread" not in client.get("/journal").get_data(as_text=True)


def test_mark_expired_has_a_tip(client):
    key = unquote(_open_spread(client).location.split("/journal/trades/")[1].split("?")[0])
    html = client.get("/journal/trades/" + quote(key, safe=":")).get_data(as_text=True)
    assert 'id="tip-trade-mark-expired"' in html


def test_the_form_has_tips_on_fees_and_buy_sell(client):
    html = client.get("/journal/manual/new").get_data(as_text=True)
    assert 'id="tip-manual-fees"' in html and 'id="tip-manual-buy-sell"' in html
    # The fees tip sits beside the label, not inside it
    label = html[html.index("<label>Fees, total"):].split("</label>")[0]
    assert "tip" not in label
