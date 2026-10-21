"""The Journal's Manage this trade (#158): a manual bot or custom rules for any open
options trade, changed or removed, the wait while a close works, the assignment hint."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

import pytest
from sqlalchemy import select
from tests.services.bot_fakes import FakeChain, FakeHub, FakeTasty, make
from tests.web.test_journal_pages import FakeTastyTrade

from optionsjourney.app import create_app
from optionsjourney.bots.manual import ManualRules
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_entry, bot_loops, manual_attach, manual_bots
from optionsjourney.trading.orders import plain
from optionsjourney.web.routes import journal as journal_routes

D = Decimal
ORIGIN = {"Origin": "http://localhost"}
NOW = datetime(2026, 10, 13, 14, 30, tzinfo=UTC)
OPENED = NOW - timedelta(days=5)
SPX_PUT = "SPXW  261030P05700000"
SPY_PUT = "SPY   261030P00570000"
WARNING = "SPY options can be assigned. This has no exit time."
WAIT = "A close is working on this trade. You can change its rules once it finishes."


def _row(tid, symbol, root, order_id, action="Sell to Open", price="2.00", at=OPENED):
    return {
        "id": tid, "symbol": symbol, "instrument-type": "Equity Option",
        "underlying-symbol": root, "transaction-type": "Trade", "action": action,
        "quantity": "1", "price": price, "value": str(float(price) * 100),
        "value-effect": "Credit" if action.startswith("Sell") else "Debit",
        "executed-at": at.isoformat(), "order-id": order_id,
    }  # fmt: skip


SPX = "tastytrade:5WX01234:10"
SPY = "tastytrade:5WX01234:20"
CLOSED = "tastytrade:5WX01234:30"
CLOSED_PUT = "SPXW  261030P05600000"
ROWS = [
    _row(1, SPX_PUT, "SPXW", 10), _row(2, SPY_PUT, "SPY", 20),
    _row(3, CLOSED_PUT, "SPXW", 30),
    _row(4, CLOSED_PUT, "SPXW", 31, "Buy to Close", "1.00", OPENED + timedelta(days=1)),
]  # fmt: skip


@pytest.fixture
def client(settings, fake_credentials, monkeypatch):
    broker = FakeTastyTrade(rows=ROWS)
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/sync", headers=ORIGIN)
    factory = app.extensions["optionsjourney"].session_factory
    tasty = FakeTasty()
    deps = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: NOW, background=lambda fn, *a: fn(*a),
    )  # fmt: skip
    deps.tasty = tasty
    tasty.held[plain(SPX_PUT)] = tasty.held[plain(SPY_PUT)] = -1
    monkeypatch.setattr(bot_loops, "deps_for", lambda services: deps)
    monkeypatch.setattr(journal_routes, "_now", lambda: NOW)
    c.factory, c.deps = factory, deps
    return c


def url(key, tail=""):
    return "/journal/trades/" + quote(key, safe=":") + tail


def page(client, key, tail=""):
    return client.get(url(key, tail)).get_data(as_text=True)


def runs(client):
    with client.factory() as session:
        return list(session.scalars(select(BotRun).order_by(BotRun.id)))


def post(client, key, data, tail="/manage"):
    return client.post(url(key, tail), data=data, headers=ORIGIN)


def test_manage_offered_for_old_open_trade(client):
    html = page(client, SPX)
    assert "Manage this trade" in html and url(SPX, "/manage") in html
    assert "Hand this trade to a bot" not in html  # today's hand-over: _adoptable's
    assert "Only an options trade of up to 4 legs, opened today" in html
    assert client.get(url(SPX, "/manage")).status_code == 200


def test_not_offered_for_bot_or_closed_or_stock(client):
    assert "Manage this trade" not in page(client, CLOSED)
    assert client.get(url(CLOSED, "/manage")).status_code == 404
    # A trade a scheduled bot manages: its own Manage chip, not this page
    bot_id = make(client.factory, live=True)
    with client.factory() as session:
        session.add(BotRun(
            bot_id=bot_id, key="x", trading_date=NOW.date(), entry_time="09:40",
            status="filled", live=True, account_number="5WX01234", broker="tastytrade",
            broker_order_id="10", created_at=NOW, updated_at=NOW,
        ))  # fmt: skip
        session.commit()
    assert "Manage this trade" not in page(client, SPX)
    assert client.get(url(SPX, "/manage")).status_code == 404
    entry = type("E", (), {})()
    entry.bot = None
    entry.position = type(
        "P",
        (),
        {
            "status": type("S", (), {"value": "open"})(),
            "live_legs": [type("L", (), {"option": None})()],
        },
    )()
    assert journal_routes._manageable(entry) is False  # shares: nothing to manage


def test_pick_manual_bot_attaches(client):
    manual_id = manual_bots.save(
        client.factory, None, ManualRules("Half off", target_value=D("50")), NOW
    )
    r = post(client, SPX, {"manual_bot_id": manual_id})
    assert r.status_code == 302
    (run,) = runs(client)
    assert run.status == "filled" and run.broker_order_id == "10"
    html = page(client, SPX, "/manage")
    assert "Half off" in html and "Remove its rules" in html


def test_custom_rules_not_saved_as_template(client):
    r = post(client, SPX, {"custom": "1", "target_value": "40", "target_unit": "pct"})
    assert r.status_code == 302
    assert manual_bots.listing(client.factory) == []
    (run,) = runs(client)
    with client.factory() as session:
        target = session.scalar(select(BotExit).where(BotExit.run_id == run.id))
    assert target.kind == "target" and target.limit == D("1.20")


def test_custom_rules_bad_number_rerenders(client):
    r = post(client, SPX, {"custom": "1", "target_value": "abc"})
    assert r.status_code == 400 and 'value="abc"' in r.get_data(as_text=True)
    assert runs(client) == []


def test_assignment_warning_spy_only(client):
    post(client, SPY, {"custom": "1", "target_value": "50"})
    html = page(client, SPY, "/manage")
    assert html.count(WARNING) == 1 and "Add an exit time" in html
    post(client, SPX, {"custom": "1", "target_value": "50"})
    html = page(client, SPX, "/manage")
    assert "can be assigned" not in html and "Add an exit time" not in html


def test_assignment_warning_gone_with_exit_time(client):
    post(client, SPY, {"custom": "1", "target_value": "50", "exit_on": "on",
                       "close_dte": "1", "close_time": "15:00"})  # fmt: skip
    assert "can be assigned" not in page(client, SPY, "/manage")


def test_add_exit_time_defaults_expiry_1545(client):
    post(client, SPY, {"custom": "1", "target_value": "50"})
    old = runs(client)[0].id
    post(client, SPY, {"add_exit_time": "1"})
    current = [r for r in runs(client) if r.status == "filled"]
    assert len(current) == 1 and current[0].id != old
    rules = manual_attach.rules_of_run(client.deps, current[0].id)
    assert (rules.close_time, rules.close_dte, rules.target_value) == ("15:45", 0, D("50"))
    assert "can be assigned" not in page(client, SPY, "/manage")


def test_change_waits_while_close_works(client):
    post(client, SPX, {"custom": "1", "target_value": "50"})
    (run,) = runs(client)
    with client.factory() as session:
        session.add(BotExit(run_id=run.id, group_position=0, kind="close", state="firing",
                            short_symbol=SPX_PUT, long_symbol="", contracts=1, log=[],
                            created_at=NOW, updated_at=NOW))  # fmt: skip
        session.commit()
    before = list(client.deps.tasty.calls)
    html = page(client, SPX, "/manage")
    assert WAIT in html and "Remove its rules" not in html
    for tail, data in (("/manage", {"custom": "1", "target_value": "25"}),
                       ("/manage/remove", {})):  # fmt: skip
        r = post(client, SPX, data, tail)
        assert WAIT in client.get(r.location).get_data(as_text=True)
    assert client.deps.tasty.calls == before
    assert [r.status for r in runs(client)] == ["filled"]


def test_remove_returns_trade_to_unmanaged(client):
    post(client, SPX, {"custom": "1", "target_value": "50"})
    assert "Remove its rules" in page(client, SPX, "/manage")
    post(client, SPX, {}, "/manage/remove")
    assert [r.status for r in runs(client)] == [manual_attach.HANDED_BACK]
    html = page(client, SPX)
    assert "Manage this trade…" in html and "badge--bot" not in html
    assert "Remove its rules" not in page(client, SPX, "/manage")
    post(client, SPX, {"custom": "1", "target_value": "25"})  # rules can be given again
    assert [r.status for r in runs(client)] == [manual_attach.HANDED_BACK, "filled"]


def test_a_per_trade_run_is_managed_from_the_gear_not_a_chip(client):
    post(client, SPX, {"custom": "1", "target_value": "50"})
    html = client.get("/journal").get_data(as_text=True)
    assert "chip--manage" not in html and url(SPX, "/panel") in html


def _hidden_bot(client):
    post(client, SPX, {"custom": "1", "target_value": "50"})
    (run,) = runs(client)
    return run.bot_id


def test_a_per_trade_bots_page_and_actions_are_refused(client):
    """Final review I1: the hidden per-trade bot is never reachable as a bot."""
    from optionsjourney.db.models import OrderLog
    from optionsjourney.services import bot_store

    bot_id = _hidden_bot(client)
    before = list(client.deps.tasty.calls)
    with client.factory() as session:
        logged = len(list(session.scalars(select(OrderLog))))
    assert client.get(f"/automation/{bot_id}").status_code == 404
    assert client.get(f"/automation/{bot_id}/live").status_code == 404
    for tail, data in (("arm", {"on": "1"}), ("live", {"on": "1"}), ("preview", {}),
                       ("trade-now", {"confirm": "TRADE"}), ("delete", {})):  # fmt: skip
        r = client.post(f"/automation/{bot_id}/{tail}", data=data, headers=ORIGIN)
        assert r.status_code == 404, tail
    r = client.post("/automation/save", data={"id": str(bot_id), "name": "x"}, headers=ORIGIN)
    assert r.status_code == 404
    assert client.deps.tasty.calls == before
    with client.factory() as session:
        assert len(list(session.scalars(select(OrderLog)))) == logged
    bot = bot_store.load(client.factory, bot_id)
    assert bot.per_trade and not bot.enabled and not bot.archived


def test_trade_now_refuses_a_per_trade_bot(client):
    bot_id = _hidden_bot(client)
    before = list(client.deps.tasty.calls)
    run_id, problem = bot_entry.trade_now(client.deps, bot_id, "TRADE")
    assert run_id is None and problem
    assert client.deps.tasty.calls == before and len(runs(client)) == 1


def test_saving_a_per_trade_bot_keeps_it_per_trade(client):
    from dataclasses import replace

    from optionsjourney.bots.definition import EntryTimeDef
    from optionsjourney.services import bot_store

    bot_id = _hidden_bot(client)
    bot = bot_store.load(client.factory, bot_id)
    saved, problems = bot_store.save(client.factory, replace(bot, per_trade=False, entry_times=(EntryTimeDef("09:47"),)), NOW, {})
    assert saved == bot_id and not problems, problems
    assert bot_store.load(client.factory, bot_id).per_trade


def test_a_per_trade_badge_links_to_manage_and_its_runs_page_has_no_back_link(client):
    bot_id = _hidden_bot(client)
    href = f'class="badge badge--bot" href="{url(SPX, "/manage")}"'
    assert href in client.get("/journal").get_data(as_text=True)
    assert f'class="badge badge--bot" href="{url(SPX, "/manage")}' in page(client, SPX)
    html = client.get(f"/automation/{bot_id}/runs").get_data(as_text=True)
    assert "Back to the bot" not in html and f"/automation/{bot_id}\"" not in html


MANAGE_TIPS = (
    "manage-profit-target", "manage-stop-loss", "manage-exit-time", "manage-alerts",
)  # fmt: skip


def test_the_manage_page_explains_its_custom_rule_types(client):
    html = page(client, SPX, "/manage")
    for tip in MANAGE_TIPS:
        assert f'id="tip-{tip}"' in html, tip
    assert 'title="' not in html.split("Or custom rules")[1].split("</form>")[0]


def test_the_panel_explains_the_hand_over_and_custom_rules(client):
    html = client.get(url(SPX, "/panel"), query_string={"back": "/journal"}).get_data(as_text=True)
    for tip in MANAGE_TIPS:
        assert f'id="tip-{tip}"' in html, tip


def test_the_manual_bot_form_explains_its_rule_types(client):
    html = client.get("/automation/manual/new").get_data(as_text=True)
    for tip in ("manual-profit-target", "manual-stop-loss", "manual-exit-time", "manual-alerts"):
        assert f'id="tip-{tip}"' in html, tip
