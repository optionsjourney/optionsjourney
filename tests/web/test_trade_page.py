"""The Trade page (#90 slice 2): the chain, the ticket and Review order."""

import re
from datetime import UTC, datetime

import pytest
from tests.services.test_orders import FakeBroker
from tests.web.test_journal_pages import ORIGIN

from optionsjourney.app import create_app
from optionsjourney.db.models import Account
from optionsjourney.services import trade_ticket
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side
from optionsjourney.web import services


@pytest.fixture(autouse=True)
def _no_leftover_codes():
    trade_ticket._PENDING.clear()
    yield
    trade_ticket._PENDING.clear()


@pytest.fixture
def app(settings, fake_credentials):
    broker = FakeBroker(held={})
    broker.accounts = lambda: []
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    with app.app_context(), services().session_factory() as session:
        session.add(Account(broker="tastytrade", number="5WX01234", nickname="Alpha",
                            updated_at=datetime.now(UTC)))  # fmt: skip
        session.commit()
    app.broker = broker
    return app


def test_trade_page_replaces_analyze_in_the_nav(app):
    html = app.test_client().get("/trade?ticker=SPX").get_data(as_text=True)
    assert "js/trade/trade_page.js" in html and 'id="pl-chart-canvas"' in html
    assert 'id="trade-working"' in html  # the working list has its own container
    assert 'href="/trade"' in html and 'href="/analyze"' not in html
    assert '"broker": "TASTYTRADE"' in html and "Alpha" in html and "5WX01234" not in html


def test_review_previews_and_never_sends(app):
    client = app.test_client()
    with app.app_context(), services().session_factory() as session:
        account_id = session.query(Account).one().id
    body = {
        "account_id": account_id, "quantity": 1, "price": 1.5, "tif": "day",
        "legs": [{"stream_symbol": ".SPXW261013P5800", "side": "sell", "ratio": 1},
                 {"stream_symbol": ".SPXW261013P5790", "side": "buy", "ratio": 1}],
    }  # fmt: skip
    got = client.post("/api/trade/review", json=body, headers=ORIGIN).get_json()
    assert got["status"] == "success" and got["action"] == "preview" and got["ok"]
    assert "place" not in [c[0] for c in app.broker.calls]


def test_review_refuses_bad_input(app):
    client = app.test_client()
    assert client.post("/api/trade/review", json=[], headers=ORIGIN).status_code == 400
    missing = client.post("/api/trade/review", json={"account_id": 999}, headers=ORIGIN)
    assert missing.status_code == 404
    with app.app_context(), services().session_factory() as session:
        account_id = session.query(Account).one().id
    zero = client.post("/api/trade/review", headers=ORIGIN, json={
        "account_id": account_id, "quantity": 1, "price": 0, "tif": "day",
        "legs": [{"stream_symbol": ".SPXW261013P5800", "side": "sell", "ratio": 1}],
    }).get_json()  # fmt: skip
    assert zero["action"] == "refuse" and "above 0" in zero["message"]


BODY = {
    "quantity": 1, "price": 1.5, "tif": "day",
    "legs": [{"stream_symbol": ".SPXW261013P5800", "side": "sell", "ratio": 1},
             {"stream_symbol": ".SPXW261013P5790", "side": "buy", "ratio": 1}],
}  # fmt: skip


def _account_id(app):
    with app.app_context(), services().session_factory() as session:
        return session.query(Account).order_by(Account.id).first().id


def _review(app, client):
    body = {**BODY, "account_id": _account_id(app)}
    return client.post("/api/trade/review", json=body, headers=ORIGIN).get_json()


def _places(app):
    return [c for c in app.broker.calls if c[0] == "place"]


def test_confirm_sends_the_reviewed_order(app):
    client = app.test_client()
    reviewed = _review(app, client)
    assert reviewed["code"] and reviewed["summary"] and not _places(app)
    sent = {"account_id": _account_id(app), "code": reviewed["code"]}
    got = client.post("/api/trade/confirm", json=sent, headers=ORIGIN).get_json()
    assert got["status"] == "success" and got["action"] == "send" and got["ok"]
    assert got["order_id"] == "777" and len(_places(app)) == 1
    assert "5WX01234" not in str(got)


def test_confirm_needs_a_code_for_that_account(app):
    client = app.test_client()
    first = _account_id(app)
    bad = client.post("/api/trade/confirm", json={"account_id": first, "code": "nope"},
                      headers=ORIGIN).get_json()  # fmt: skip
    assert bad["action"] == "refuse" and not bad["ok"] and not _places(app)
    with app.app_context(), services().session_factory() as session:
        other = Account(broker="tastytrade", number="5WY99999", updated_at=datetime.now(UTC))
        session.add(other)
        session.commit()
        other_id = other.id
    code = _review(app, client)["code"]
    wrong = client.post("/api/trade/confirm", json={"account_id": other_id, "code": code},
                        headers=ORIGIN).get_json()  # fmt: skip
    assert wrong["action"] == "refuse" and not wrong["ok"] and not _places(app)


def test_working_cancel_replace_routes(app):
    client = app.test_client()
    account_id = _account_id(app)
    code = _review(app, client)["code"]
    client.post("/api/trade/confirm", json={"account_id": account_id, "code": code}, headers=ORIGIN)
    leg = BrokerLeg("SPXW  261013P05800000", Side.SELL, True, 1, 1)
    app.broker.working = [BrokerOrder("777", OrderStatus.WORKING, (leg,),
                                      entered_at=datetime.now(UTC))]  # fmt: skip
    got = client.get(f"/api/trade/working?account_id={account_id}").get_json()
    assert got["status"] == "success" and got["orders"][0]["yours"] is True
    assert "5WX01234" not in str(got)
    new_price = {"account_id": account_id, "order_id": "777", "price": 1.6}
    rep = client.post("/api/trade/replace", json=new_price, headers=ORIGIN).get_json()
    assert rep["status"] == "success" and rep["ok"]
    can = client.post("/api/trade/cancel", headers=ORIGIN,
                      json={"account_id": account_id, "order_id": "777"}).get_json()  # fmt: skip
    assert can["status"] == "success" and can["ok"]


def test_working_says_so_when_the_broker_cannot_be_read(app):
    from optionsjourney.brokers.http import BrokerError

    app.broker.errors["working"] = BrokerError("down")
    got = app.test_client().get(f"/api/trade/working?account_id={_account_id(app)}")
    assert got.status_code == 502
    assert got.get_json()["message"] == "Couldn't read your working orders from the broker."


def test_unknown_accounts_are_404_on_every_route(app):
    client = app.test_client()
    assert client.get("/api/trade/working?account_id=999").status_code == 404
    assert client.get("/api/trade/working").status_code == 404
    for path in ("confirm", "cancel", "replace", "bots", "bot-entry"):
        r = client.post(f"/api/trade/{path}", json={"account_id": 999}, headers=ORIGIN)
        assert r.status_code == 404


def test_true_is_not_an_account_id(app):
    client = app.test_client()
    for path, extra in (("review", BODY), ("confirm", {"code": "x"})):
        r = client.post(f"/api/trade/{path}", json={"account_id": True, **extra}, headers=ORIGIN)
        assert r.status_code == 404
    assert not _places(app)


@pytest.mark.parametrize("path", ["review", "confirm", "cancel", "replace", "bots", "bot-entry"])
@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}])
def test_order_routes_refuse_other_sites(app, path, headers):
    body = {"account_id": _account_id(app), "code": "x", "order_id": "777", "price": 1.6, **BODY}
    r = app.test_client().post(f"/api/trade/{path}", json=body, headers=headers)
    assert r.status_code == 403
    assert not [c for c in app.broker.calls if c[0] in ("preview", "place", "replace", "cancel")]


# --- Manage with a bot (bots slice 15) ----------------------------------------------------


@pytest.fixture
def botting(app, monkeypatch):
    """The page's app with a live iron condor bot on its account and the bot machinery
    (broker, quotes, clock) faked; the entry runs inline."""
    from tests.services import test_bot_ticket as ticket_tests
    from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make

    from optionsjourney.services import bot_entry, bot_loops

    with app.app_context():
        factory = services().session_factory
    tasty = FakeTasty()
    deps = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: OPEN_TIME, background=lambda fn, *a: fn(*a),
    )  # fmt: skip
    deps.tasty = tasty
    monkeypatch.setattr(bot_loops, "deps_for", lambda s: deps)
    app.bot_id = make(factory, live=True)
    app.deps = deps
    app.body = ticket_tests.quoted(deps, ticket_tests.ticket(bot_id=app.bot_id))
    return app


def test_the_bot_picker_lists_the_bots_and_sends_nothing(botting):
    body = {"account_id": _account_id(botting), **botting.body}
    got = botting.test_client().post("/api/trade/bots", json=body, headers=ORIGIN).get_json()
    assert got["status"] == "success" and got["problem"] is None
    (bot,) = got["bots"]
    assert bot["id"] == botting.bot_id and bot["problem"] is None and bot["name"] == "IC"
    assert (
        bot["exits"][0] == "Call side: sell 5910C Oct 13 / buy 5985C Oct 13 · stop 95% · no target"
    )
    assert bot["futures"] is False
    assert botting.deps.tasty.calls == []


def test_the_bot_picker_flags_a_futures_bot(botting):
    """Bots slice 17: the page words a futures bot's send as a resting limit."""
    from tests.services.bot_fakes import fut_stream, make_futures, on_futures

    with botting.app_context():
        factory = services().session_factory
    on_futures(botting.deps)
    es = make_futures(factory)
    body = {
        "account_id": _account_id(botting),
        "ticker": "/ES",
        "quantity": 1,
        "legs": [{"stream_symbol": fut_stream(7000, "put"), "side": "sell", "ratio": 1}],
    }
    got = botting.test_client().post("/api/trade/bots", json=body, headers=ORIGIN).get_json()
    rows = {row["id"]: row for row in got["bots"]}
    assert rows[es]["futures"] is True and rows[es]["problem"] is None
    assert rows[es]["exits"] == ["Put: sell 7000P Nov 20 · target 50% · no stop (futures)"]
    assert rows[botting.bot_id]["problem"] == "It trades SPX, not /ES."


def _reviewed(client, app):
    """Review order on the ticket, as the page does first: the body with its code."""
    body = {"account_id": _account_id(app), **app.body}
    review = client.post("/api/trade/review", json={**body, "price": 4.9, "tif": "day"},
                         headers=ORIGIN).get_json()  # fmt: skip
    assert review["ok"] and review["code"], review
    return {**body, "code": review["code"]}


def _bot_openings(app):
    return [c for c in app.deps.tasty.calls if c[0] == "place"
            and c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip


def test_send_then_the_bot_send_places_one_order(botting):
    client = botting.test_client()
    body = _reviewed(client, botting)
    sent = client.post("/api/trade/confirm", json=body, headers=ORIGIN).get_json()
    assert sent["ok"] and sent["action"] == "send"
    bot = client.post("/api/trade/bot-entry", json=body, headers=ORIGIN).get_json()
    assert not bot["ok"] and "already been used" in bot["message"]
    assert _bot_openings(botting) == []


def test_the_bot_send_then_send_places_one_order(botting):
    client = botting.test_client()
    body = _reviewed(client, botting)
    bot = client.post("/api/trade/bot-entry", json=body, headers=ORIGIN).get_json()
    assert bot["ok"], bot
    sent = client.post("/api/trade/confirm", json=body, headers=ORIGIN).get_json()
    assert not sent["ok"] and "already been used" in sent["message"]
    assert "place" not in [c[0] for c in botting.broker.calls]
    assert len(_bot_openings(botting)) == 1


def test_send_as_the_bots_entry_once(botting):
    client = botting.test_client()
    body = _reviewed(client, botting)
    got = client.post("/api/trade/bot-entry", json=body, headers=ORIGIN).get_json()
    assert got["status"] == "success" and got["ok"], got
    assert got["run_url"] == f"/automation/runs/{got['run_id']}"
    page = client.get(got["run_url"]).get_data(as_text=True)
    assert "From the Trade page" in page
    again = client.post("/api/trade/bot-entry", json=body, headers=ORIGIN).get_json()
    assert not again["ok"] and "already sent" in again["message"]
    opening = [c for c in botting.deps.tasty.calls if c[0] == "place"
               and c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip
    assert len(opening) == 1
    assert "place" not in [c[0] for c in botting.broker.calls]  # the plain ticket's broker


def test_risk_graph_drawer_sits_above_the_page_banners(app):
    # Its Close button is at the top: the gateway and demo banners (z-index 100) covered it
    # while the drawer was z-40. Same layer as the Journal's risk drawer (.drawer, 400).
    html = app.test_client().get("/trade").get_data(as_text=True)
    drawer = re.search(r'<section id="trade-graph" class="([^"]*)"', html)
    assert drawer and "z-[400]" in drawer.group(1).split()
