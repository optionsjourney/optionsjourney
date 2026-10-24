"""Socket.IO for the chart pages (#25)."""

import pytest

from optionsjourney.app import create_app

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    app.extensions["optionsjourney"].chart_feed._autostart = False
    return app


def socket_client(app):
    return app.extensions["optionsjourney"].socketio.test_client(app)


def test_candle_subscriptions_are_checked_and_cleaned_up(app):
    client = socket_client(app)
    client.emit("subscribe_candles", {"symbol": "/ES", "interval": "5m"})
    received = client.get_received()
    assert received[0]["name"] == "chart_error"
    client.emit("subscribe_candles", {"symbol": "spy", "interval": "5m"})
    feed = app.extensions["optionsjourney"].chart_feed
    assert feed.wanted() == {("SPY", "5m")}
    client.disconnect()
    assert feed.wanted() == set()


def test_a_drawing_change_reaches_charts_watching_it(app):
    client = socket_client(app)
    client.emit("watch_drawings", {"symbol": "SPX", "interval": "5m"})
    line = {"id": "d1", "type": "hline", "p": [{"time": 1, "price": 7000}]}
    app.test_client().put(
        "/api/chart_drawings",
        json={"symbol": "SPX", "interval": "5m", "drawings": [line]},
        headers=ORIGIN,
    )
    received = client.get_received()
    assert received == [
        {
            "name": "chart_drawings_changed",
            "args": [{"symbol": "SPX", "interval": "5m"}],
            "namespace": "/",
        }
    ]


def test_a_new_alert_pops_up_on_every_page(app):
    """#275: the server sends `alert_fired`, which the pages show as a popup."""
    from datetime import UTC, datetime

    from optionsjourney.services import app_alerts

    client = socket_client(app)
    with app.app_context():
        app_alerts.add(
            app.extensions["optionsjourney"].session_factory, dedupe_key="k", kind="user_alert",
            title="SPX went above 7,600.00", detail="now 7,646.98", link="/trade?ticker=SPX",
            now=datetime.now(UTC), send=False,
        )  # fmt: skip
    [fired] = [m for m in client.get_received() if m["name"] == "alert_fired"]
    payload = fired["args"][0]
    assert payload["title"] == "SPX went above 7,600.00" and payload["link"] == "/trade?ticker=SPX"
