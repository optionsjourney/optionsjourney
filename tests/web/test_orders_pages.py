from datetime import UTC, datetime

import pytest

from optionsjourney.app import create_app
from optionsjourney.db.models import OrderLog
from optionsjourney.services import orders
from optionsjourney.web import services

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app


def factory(app):
    with app.app_context():
        return services().session_factory


def test_the_menu_offers_both_switches_and_no_banner_by_default(app):
    html = app.test_client().get("/journal").get_data(as_text=True)
    assert "No new trades" in html and "Freeze all orders" in html
    assert "banner--freeze" not in html and "banner--no-new" not in html


def test_no_new_trades_turns_on_and_shows_its_banner(app):
    client = app.test_client()
    client.post("/orders/switch/no_new_trades", data={"on": "1"}, headers=ORIGIN)
    assert orders.switches(factory(app)).no_new_trades
    html = client.get("/journal").get_data(as_text=True)
    assert "banner--no-new" in html
    assert "bots won't open trades. Open trades are still watched." in html.replace("&#39;", "'")


def test_freeze_asks_first_and_explains(app):
    client = app.test_client()
    page = client.get("/orders/freeze").get_data(as_text=True)
    assert "Freeze all orders?" in page
    assert "Orders already at your broker stay there and can still fill" in page
    assert "no new stops, no re-arming, no profit targets, no time exits, no closes." in page
    client.post("/orders/switch/freeze", data={"on": "1"}, headers=ORIGIN)
    html = client.get("/journal").get_data(as_text=True).replace("&#39;", "'")
    assert "banner--freeze" in html
    assert "the app isn't sending orders or watching your trades" in html
    client.post("/orders/switch/freeze", data={"on": "0"}, headers=ORIGIN)
    assert not orders.switches(factory(app)).freeze


def test_the_banner_also_shows_on_the_ported_pages(app):
    client = app.test_client()
    client.post("/orders/switch/freeze", data={"on": "1"}, headers=ORIGIN)
    assert "banner--freeze" in client.get("/charts").get_data(as_text=True)


def test_an_unknown_switch_is_404(app):
    response = app.test_client().post("/orders/switch/everything", data={"on": "1"}, headers=ORIGIN)
    assert response.status_code == 404


def test_the_order_log_lists_attempts_and_clears_unclear_ones(app):
    with factory(app)() as session:
        row = OrderLog(created_at=datetime.now(UTC), broker="tastytrade", account_number="5WX01234",
                       origin="you", action="send", opening=False, quantity=1, order_type="limit",
                       order={}, result="Unclear: Lost the connection", ok=False)  # fmt: skip
        session.add(row)
        session.commit()
        row_id = row.id
    client = app.test_client()
    html = client.get("/orders").get_data(as_text=True)
    assert "Lost the connection" in html and f"/orders/{row_id}/clear" in html
    client.post(f"/orders/{row_id}/clear", headers=ORIGIN)
    assert "Cleared by you" in client.get("/orders").get_data(as_text=True)


def test_the_account_filter_lists_accounts_by_last_four_and_filters(app):
    with factory(app)() as session:
        for number, result in (("5WX01234", "Sent one"), ("12345678", "Sent two")):
            session.add(OrderLog(created_at=datetime.now(UTC), broker="tastytrade",
                                 account_number=number, origin="you", action="send",
                                 opening=False, quantity=1, order_type="limit", order={},
                                 result=result, ok=True))  # fmt: skip
        session.commit()
    client = app.test_client()
    html = client.get("/orders").get_data(as_text=True)
    assert "TastyTrade ••1234" in html and "TastyTrade ••5678" in html
    filtered = client.get("/orders?account=5WX01234").get_data(as_text=True)
    assert "Sent one" in filtered and "Sent two" not in filtered


def test_the_switch_redirect_refuses_backslash_tricks(app):
    client = app.test_client()
    response = client.post(
        "/orders/switch/freeze", data={"on": "0", "next": "/\\evil.com"}, headers=ORIGIN
    )
    assert response.location == "/orders"
    response = client.post(
        "/orders/switch/freeze", data={"on": "0", "next": "/journal"}, headers=ORIGIN
    )
    assert response.location == "/journal"


def _banner(html, kind):
    start = html.index(f"banner--{kind}")
    return html[start : html.index("</div>", start)]


@pytest.mark.parametrize(("switch", "kind"), [("freeze", "freeze"), ("no_new_trades", "no-new")])
def test_each_banner_links_to_the_order_log(app, switch, kind):
    client = app.test_client()
    client.post(f"/orders/switch/{switch}", data={"on": "1"}, headers=ORIGIN)
    assert 'href="/orders"' in _banner(client.get("/journal").get_data(as_text=True), kind)


def test_settings_links_to_the_order_log(app):
    html = app.test_client().get("/settings").get_data(as_text=True)
    main = html[html.index("<h1>Settings</h1>") :]
    assert 'href="/orders"' in main


def test_the_order_log_and_freeze_have_tips_that_link_to_the_guide(app):
    with factory(app)() as session:
        session.add(OrderLog(created_at=datetime.now(UTC), broker="tastytrade",
                             account_number="5WX01234", origin="you", action="send",
                             opening=False, quantity=1, order_type="limit", order={},
                             result="Unclear: Lost the connection", ok=False))  # fmt: skip
        session.commit()
    client = app.test_client()
    html = client.get("/orders").get_data(as_text=True)
    for tip in ("orders-what", "orders-result", "orders-from"):
        assert f'id="tip-{tip}"' in html
    assert "/help/pausing-trading#unclear-check-your-broker" in html
    freeze = client.get("/orders/freeze").get_data(as_text=True)
    assert 'id="tip-freeze"' in freeze and "/help/pausing-trading#freeze-all-orders" in freeze


@pytest.mark.parametrize(
    "switch, tip", [("freeze", "banner-unfreeze"), ("no_new_trades", "banner-no-new")]
)
def test_each_banner_switch_has_a_tip(app, switch, tip):
    client = app.test_client()
    client.post(f"/orders/switch/{switch}", data={"on": "1"}, headers=ORIGIN)
    html = client.get("/journal").get_data(as_text=True)
    assert f'aria-describedby="tip-{tip}"' in html and f'id="tip-{tip}"' in html
