import json

from optionsjourney.app import create_app
from optionsjourney.demo.clock import DEMO_DAY
from optionsjourney.demo.tour_trade import put_spread_tray


def _demo(app):
    return app.extensions["optionsjourney"].demo


def test_put_spread_is_two_puts_25_points_apart(demo_app):
    demo = _demo(demo_app)
    tray = put_spread_tray(demo.market, demo.clock)
    short, long = tray["cells"]
    assert tray["ticker"] == "SPX"
    assert (short["side"], long["side"]) == ("sell", "buy")
    assert short["type"] == long["type"] == "put"
    assert short["strike"] - long["strike"] == 25
    assert short["expiration"] == long["expiration"] == DEMO_DAY.isoformat()
    assert -0.25 < short["delta"] < -0.08
    assert short["ask"] > long["ask"] > 0


def test_trade_page_has_the_tray_only_at_step_four(demo_app):
    demo = _demo(demo_app)
    client = demo_app.test_client()
    demo.tour.current = 1
    assert b"data-tour-tray" not in client.get("/trade").data
    demo.tour.current = 4
    html = client.get("/trade").get_data(as_text=True)
    assert "data-tour-tray" in html
    raw = html.split("data-tour-tray='")[1].split("'")[0].replace("&#34;", '"')
    assert len(json.loads(raw)["cells"]) == 2


def test_local_trade_page_has_no_tour_tray(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    assert b"data-tour-tray" not in app.test_client().get("/trade").data
