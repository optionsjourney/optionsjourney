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
