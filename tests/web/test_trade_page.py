"""The Trade page (#90 slice 2): the chain, the ticket and Review order."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.services.test_orders import FakeBroker
from tests.web.test_journal_pages import ORIGIN

from optionsjourney.app import create_app
from optionsjourney.db.models import Account, Idea
from optionsjourney.services import ideas, orders, trade_ticket
from optionsjourney.trading.orders import (
    BrokerLeg,
    BrokerOrder,
    OrderStatus,
    Side,
)
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
    assert got["orders"][0]["owner"]["kind"] == "you" and got["orders"][0]["can_change"]
    assert "5WX01234" not in str(got)
    new_price = {"account_id": account_id, "order_id": "777", "price": 1.6}
    rep = client.post("/api/trade/replace", json=new_price, headers=ORIGIN).get_json()
    assert rep["status"] == "success" and rep["ok"]
    can = client.post("/api/trade/cancel", headers=ORIGIN,
                      json={"account_id": account_id, "order_id": "777"}).get_json()  # fmt: skip
    assert can["status"] == "success" and can["ok"]


def test_an_order_placed_at_the_broker_is_read_only(app):
    client = app.test_client()
    account_id = _account_id(app)
    leg = BrokerLeg("SPXW  261013P05800000", Side.SELL, True, 1, 1)
    app.broker.working = [BrokerOrder("900", OrderStatus.WORKING, (leg,), price=Decimal("1.50"))]
    row = client.get(f"/api/trade/working?account_id={account_id}").get_json()["orders"][0]
    assert row["owner"]["kind"] == "outside" and row["yours"] is False
    assert not row["can_change"] and not row["can_cancel"] and row["price"] == 1.5
    outside = "Placed outside the app. Change or cancel it at your broker."
    assert row["reason"] == outside
    new_price = {"account_id": account_id, "order_id": "900", "price": 1.6}
    rep = client.post("/api/trade/replace", json=new_price, headers=ORIGIN).get_json()
    can = client.post("/api/trade/cancel", headers=ORIGIN,
                      json={"account_id": account_id, "order_id": "900"}).get_json()  # fmt: skip
    for result in (rep, can):
        assert not result["ok"] and result["message"] == outside, result
    assert not [c for c in app.broker.calls if c[0] in ("replace", "cancel")]


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


# --- Trade Ideas (#292): trading an idea retires it ----------------------------------------

IDEA_SHORT = "SPXW  301220P05800000"
IDEA_LONG = "SPXW  301220P05790000"


def _card(legs=None):
    return {"label": "", "qty": 2, "entry_unit": -1.25, "locked": True, "multiplier": 100,
            "legs": legs or [{"symbol": IDEA_SHORT, "side": "sell", "ratio": 1},
                             {"symbol": IDEA_LONG, "side": "buy", "ratio": 2}]}  # fmt: skip


def _stored(app):
    with app.app_context(), services().session_factory() as session:
        return [idea.id for idea in session.query(Idea)]


def _idea(app):
    with app.app_context():
        s = services()
        return ideas.create(s.session_factory, s.quote_hub, "SPX", "Fly", None, [_card()])["id"]


def _confirm(app, client, **extra):
    code = _review(app, client)["code"]
    body = {"account_id": _account_id(app), "code": code, **extra}
    return client.post("/api/trade/confirm", json=body, headers=ORIGIN).get_json()


def test_confirm_retires_idea_on_send(app):
    idea_id = _idea(app)
    client = app.test_client()
    got = _confirm(app, client, idea_id=idea_id)
    assert got["ok"] and got["action"] == "send"
    assert got["idea_removed"] is True and _stored(app) == []


def test_confirm_without_an_idea_says_nothing_about_one(app):
    idea_id = _idea(app)
    client = app.test_client()
    got = _confirm(app, client)
    assert got["ok"] and "idea_removed" not in got and _stored(app) == [idea_id]
    again = _confirm(app, client, idea_id=str(idea_id))  # not an int: ignored
    assert again["ok"] and "idea_removed" not in again and _stored(app) == [idea_id]


def test_confirm_keeps_idea_on_refuse(app):
    idea_id = _idea(app)
    got = app.test_client().post("/api/trade/confirm", headers=ORIGIN, json={
        "account_id": _account_id(app), "code": "nope", "idea_id": idea_id}).get_json()  # fmt: skip
    assert got["action"] == "refuse" and got["idea_removed"] is False
    assert _stored(app) == [idea_id]


def test_confirm_keeps_idea_on_dry_run(app, monkeypatch):
    """A Place-only confirm has no dry run: an ok answer that isn't a send stands in."""
    idea_id = _idea(app)
    checked = orders.Result("preview", True, "Checked, not sent.")
    monkeypatch.setattr(trade_ticket, "confirm", lambda *a, **k: checked)
    got = app.test_client().post("/api/trade/confirm", headers=ORIGIN, json={
        "account_id": _account_id(app), "code": "x", "idea_id": idea_id}).get_json()  # fmt: skip
    assert got["ok"] and got["action"] == "preview" and got["idea_removed"] is False
    assert _stored(app) == [idea_id]


def test_confirm_retire_failure_does_not_fail_trade(app, monkeypatch):
    idea_id = _idea(app)

    def broken(sf, idea_id):
        raise RuntimeError("database locked")

    monkeypatch.setattr(ideas, "retire", broken)
    got = _confirm(app, app.test_client(), idea_id=idea_id)
    assert got["status"] == "success" and got["ok"] and got["action"] == "send"
    assert got["order_id"] == "777" and got["idea_removed"] is False


def _bot_body(client, app, **extra):
    body = {"account_id": _account_id(app), **app.body}
    review = client.post("/api/trade/review", json={**body, "price": 4.9, "tif": "day"},
                         headers=ORIGIN).get_json()  # fmt: skip
    return {**body, "code": review["code"], **extra}


def test_bot_entry_retires_on_ok(botting):
    idea_id = _idea(botting)
    client = botting.test_client()
    got = client.post("/api/trade/bot-entry", json=_bot_body(client, botting, idea_id=idea_id),
                      headers=ORIGIN).get_json()  # fmt: skip
    assert got["ok"] and got["idea_removed"] is True and _stored(botting) == []


def test_bot_entry_keeps_idea_when_refused(botting):
    idea_id = _idea(botting)
    client = botting.test_client()
    body = _bot_body(client, botting, idea_id=idea_id, token="bad")
    got = client.post("/api/trade/bot-entry", json=body, headers=ORIGIN).get_json()
    assert not got["ok"] and got["idea_removed"] is False and _stored(botting) == [idea_id]


def _page_idea(html):
    found = re.search(r"data-idea='([^']*)'", html)
    return json.loads(found.group(1)) if found else None


def test_trade_page_renders_idea_data(app):
    idea_id = _idea(app)
    html = app.test_client().get(f"/trade?idea={idea_id}&ticker=SPX").get_data(as_text=True)
    got = _page_idea(html)
    assert got["id"] == idea_id and got["label"] == "Fly" and got["ticker"] == "SPX"
    assert datetime.fromisoformat(got["created_at"]).tzinfo is not None
    assert got["price"] == 1.25 and got["effect"] == "debit"
    short, long = got["tray"]
    assert short == {"expiration": "2030-12-20", "strike": 5800, "type": "put", "side": "sell",
                     "qty": 2, "bid": None, "ask": None, "stream_symbol": ".SPXW301220P5800",
                     "delta": None}  # fmt: skip
    assert long["side"] == "buy" and long["qty"] == 4 and long["strike"] == 5790


def test_trade_page_without_a_live_idea_has_no_idea_data(app):
    client = app.test_client()
    assert _page_idea(client.get("/trade?idea=999").get_data(as_text=True)) is None
    assert _page_idea(client.get("/trade?idea=abc").get_data(as_text=True)) is None
    with app.app_context():
        old = _card(legs=[{"symbol": "SPXW  240119P04800000", "side": "sell", "ratio": 1}])
        gone = ideas.create(services().session_factory, services().quote_hub, "SPX", "Old", None,
                            [old], now=datetime(2024, 1, 2, tzinfo=UTC))["id"]  # fmt: skip
    assert _page_idea(client.get(f"/trade?idea={gone}").get_data(as_text=True)) is None
