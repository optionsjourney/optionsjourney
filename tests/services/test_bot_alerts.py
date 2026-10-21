"""A bot's default alerts (spec: bots slice 9, section 4)."""

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import DefaultAlertDef
from optionsjourney.db.models import BotExit, BotRun, UserAlert
from optionsjourney.services import bot_alerts, bot_entry, bot_store, user_alerts
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make

D = Decimal
RULES = (DefaultAlertDef("pnl_pct", "above", D("15")),
         DefaultAlertDef("strike_offset", points=D("10")))  # fmt: skip


@pytest.fixture
def deps(factory, monkeypatch):
    tasty = FakeTasty()
    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    d = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: OPEN_TIME, background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty, d.placed = tasty, placed
    return d


def bot_with(factory, live=True, rules=RULES, on=True, quiet=False):
    bot_id = make(factory, live=live)
    bot = bot_store.load(factory, bot_id)
    bot = replace(bot, use_default_alerts=on, default_alerts=rules,
                  default_alerts_in_app_only=quiet)  # fmt: skip
    assert bot_store.save(factory, bot, OPEN_TIME)[1] == {}
    return bot_id


def alerts(factory):
    with factory() as session:
        return list(session.scalars(select(UserAlert).order_by(UserAlert.id)))


def test_a_live_fill_makes_its_default_alerts_once(deps, factory):
    run_id = bot_entry.fire(deps, bot_with(factory), "09:47")
    made = alerts(factory)
    assert [(a.kind, a.rule_key) for a in made] == [
        ("bot_pl", "pnl_pct:above:15"), ("price", "strike:P:5690:10"),
        ("price", "strike:C:5910:10"),
    ]  # fmt: skip
    with factory() as session:
        credit = session.get(BotRun, run_id).fill_credit
    pl = made[0]
    assert (pl.bot_run_id, pl.direction, pl.level, pl.active, pl.quiet) == (
        run_id, "below", (credit * D("0.85")).quantize(D("0.01"), ROUND_HALF_UP), True, False,
    )  # fmt: skip
    assert pl.label == f"IC · run {run_id} · P&L ≥ +15%"
    assert [leg["qty"] for leg in pl.legs] == [-1, 1, -1, 1]
    assert (made[1].target, made[1].direction, made[1].level) == ("SPX", "below", D("5700"))
    # Again (a later tranche, a retry): nothing new
    assert bot_alerts.apply_defaults(deps, run_id) == 0
    assert len(alerts(factory)) == 3


def test_a_later_tranche_adds_nothing(deps, factory):
    root = bot_entry.fire(deps, bot_with(factory), "09:47")
    with factory() as session:
        run = session.get(BotRun, root)
        child = BotRun(bot_id=run.bot_id, key="tranche-2", trading_date=run.trading_date,
                       entry_time="09:47", status="filled", live=True, legs=run.legs,
                       filled_legs=run.filled_legs, fill_credit=run.fill_credit, contracts=1,
                       parent_run_id=root, chase_log=[], filled_at=OPEN_TIME,
                       created_at=OPEN_TIME, updated_at=OPEN_TIME)  # fmt: skip
        session.add(child)
        session.commit()
        child_id = child.id
    assert bot_alerts.apply_defaults(deps, child_id) == 0
    assert {a.bot_run_id for a in alerts(factory)} == {root}


def test_none_when_the_bot_has_them_off(deps, factory):
    bot_entry.fire(deps, bot_with(factory, on=False), "09:47")
    assert alerts(factory) == []


def test_a_preview_makes_no_default_alerts(deps, factory):
    bot_entry.fire(deps, bot_with(factory, live=False), "09:47")
    assert alerts(factory) == []


def test_a_default_alert_failure_leaves_the_exits_alone(deps, factory, monkeypatch):
    def broken(deps, run_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(bot_alerts, "apply_defaults", broken)
    run_id = bot_entry.fire(deps, bot_with(factory), "09:47")
    assert deps.placed == [run_id]
    with factory() as session:
        assert session.get(BotRun, run_id).status == "filled"


def test_a_strike_already_passed_at_the_fill_gets_no_alert(deps, factory):
    deps.hub.set_last("SPX", "5695", OPEN_TIME)  # below 5700: the put's alert would fire now
    bot_entry.fire(deps, bot_with(factory), "09:47")
    assert [a.rule_key for a in alerts(factory)] == ["pnl_pct:above:15", "strike:C:5910:10"]


def check(factory, hub, now):
    sent = []
    fired = user_alerts.check(factory, hub, lambda key: None, sent.append, now=now)
    return fired, sent


def test_a_default_alert_goes_off_as_a_routine_alert_from_its_bot(deps, factory):
    bot_id = bot_with(factory, rules=(DefaultAlertDef("pnl_pct", "above", D("15")),))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    [alert] = alerts(factory)
    hub = FakeHub()
    for leg in alert.legs:  # the shorts at 1.00, the wings at 0.05: a close cost of 1.90
        price = D("1.00") if leg["qty"] < 0 else D("0.05")
        hub.set(leg["key"], price, price, OPEN_TIME)
    fired, sent = check(factory, hub, OPEN_TIME)
    assert [e.title for e in fired] == [f"IC · run {run_id} · P&L ≥ +15%"]
    [event] = sent
    assert (event.bot_id, event.run_id, event.routine) == (bot_id, run_id, True)
    assert event.detail.startswith("close price now 1.90")
    assert event.link == f"/automation/runs/{run_id}"
    assert hub.subscribed >= {leg["key"] for leg in alert.legs}


def test_in_the_app_only_shows_but_is_never_sent(deps, factory):
    bot_id = bot_with(factory, rules=(DefaultAlertDef("pnl_pct", "above", D("15")),), quiet=True)
    bot_entry.fire(deps, bot_id, "09:47")
    [alert] = alerts(factory)
    hub = FakeHub()
    for leg in alert.legs:
        hub.set(leg["key"], D("0.05"), D("0.05"), OPEN_TIME)
    fired, sent = check(factory, hub, OPEN_TIME)
    assert len(fired) == 1 and sent == []
    assert [a.id for a in user_alerts.triggered(factory)] == [alert.id]  # shown in the app


def close_side(factory, run_id, position):
    with factory() as session:
        session.add(BotExit(run_id=run_id, group_position=position, kind="target", state="filled",
                            short_symbol="x", long_symbol="y", contracts=1, log=[],
                            created_at=OPEN_TIME, updated_at=OPEN_TIME))  # fmt: skip
        session.commit()


def test_default_alerts_are_cleared_once_the_trade_closes(deps, factory):
    run_id = bot_entry.fire(deps, bot_with(factory), "09:47")
    assert len(alerts(factory)) == 3
    hub, later = FakeHub(), OPEN_TIME + timedelta(minutes=5)
    close_side(factory, run_id, 0)
    check(factory, hub, later)
    assert all(a.active for a in alerts(factory))  # one side still open: kept
    close_side(factory, run_id, 1)
    check(factory, hub, later)
    assert all(not a.active and a.dismissed for a in alerts(factory))
    assert user_alerts.triggered(factory) == []


def test_alerts_you_set_are_never_cleared_by_the_sweep(deps, factory):
    user_alerts.create_price(factory, "SPX", "below", "5000")
    check(factory, FakeHub(), OPEN_TIME)
    assert [a.active for a in alerts(factory)] == [True]
