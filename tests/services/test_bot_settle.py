"""Bots slice 14: settling a group whose nearer legs expired (services/bot_settle.py)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import LegDef
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import (
    bot_entry,
    bot_exits,
    bot_integrity,
    bot_settle,
    bot_store,
    orders,
)
from optionsjourney.trading.orders import OrderStatus, Side
from tests.services.bot_fakes import (
    FAR,
    HELD_EXPIRATIONS,
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    make_calendar,
    sym,
)

D = Decimal


def ny(month_day, hour, minute=0, second=0):
    """New York wall-clock on a day in October 2026 (UTC-4), as UTC."""
    return datetime(2026, 10, month_day, tzinfo=UTC) + timedelta(
        hours=hour + 4, minutes=minute, seconds=second
    )


EXPIRY_EVENING = ny(20, 16, 1)  # the near leg (2026-10-20) has expired
AFTER_THE_PAUSE = ny(21, 9, 31, 30)


@pytest.fixture(autouse=True)
def _clear_firing():
    bot_exits.FIRING.clear()
    yield
    bot_exits.FIRING.clear()


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(expirations=HELD_EXPIRATIONS),
        notify=lambda e: None,
        sleep=lambda s: None,
        now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )
    d.tasty, d.seen = tasty, []
    d.notify = d.seen.append
    return d


def at(deps, when):
    deps.now = lambda: when
    return when


def exits(factory, kind=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        return list(session.scalars(query))


def sells(deps):
    return [c[-1] for c in deps.tasty.calls if c[0] in ("place", "replace")
            and c[-1]["legs"][0]["action"] == "Sell to Close"]  # fmt: skip


def calendar(deps, factory, **kw):
    """A put calendar, the 7 DTE 5690 put sold and the 14 DTE bought, for a debit."""
    bot_id = make_calendar(factory, live=True, **kw)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        assert session.get(BotRun, run_id).status == "filled"
    return bot_id, run_id


def open_trades(deps, bot_id):
    return bot_entry._open_trades(deps, bot_store.load(deps.session_factory, bot_id))


def far_quote(deps, when, bid="9.00", ask="9.20"):
    deps.hub.set(sym(5690, "put", FAR), D(bid), D(ask), when)


def test_near_legs_expire_and_the_far_leg_is_left_to_you(deps, factory):
    """The author's choice (Tracker2's): the bot never sells the far legs. They're
    recorded, the target cancelled and the watched stop ended, nothing sent, and an
    urgent safety alert goes out."""
    from optionsjourney.core import alert_format

    bot_id, run_id = calendar(deps, factory, target="25")
    at(deps, ny(20, 15, 59))
    bot_exits.monitor_pass(deps)
    bot_settle.settle_pass(deps)
    assert exits(factory, "settle") == []  # not yet expired
    at(deps, EXPIRY_EVENING)
    # decision 0048: its target rests at TastyTrade, which ends it with the near legs
    (target,) = exits(factory, "target")
    order = deps.tasty.orders[target.broker_order_id]
    deps.tasty.orders[target.broker_order_id] = replace(order, status=OrderStatus.EXPIRED)
    before = len(deps.tasty.calls)
    bot_exits.monitor_pass(deps)
    bot_settle.settle_pass(deps)
    (settle,) = exits(factory, "settle")
    assert settle.state == "left" and settle.contracts == 1
    assert [leg["symbol"] for leg in settle.legs] == [sym(5690, "put", FAR)]
    assert exits(factory, "stop")[0].state == "expired"
    assert exits(factory, "target")[0].state == "expired"
    [said] = [e for e in deps.seen if "near legs expired, far legs held" in e.title]
    assert said.title == "Cal: URGENT: Calendar's near legs expired, far legs held"
    assert "with no stop or target now" in said.detail and "sell it or keep it" in said.detail
    assert alert_format.is_safety(said.title, said.detail) and not said.routine
    for when in (ny(21, 3, 0), AFTER_THE_PAUSE, ny(21, 15, 0)):
        at(deps, when)
        far_quote(deps, when)
        bot_exits.monitor_pass(deps)
        bot_settle.settle_pass(deps)
    assert len(deps.tasty.calls) == before and sells(deps) == []  # never sold
    assert len(exits(factory, "settle")) == 1  # said once
    assert open_trades(deps, bot_id) == 1  # still held: still counts
    assert [f.check for f in bot_integrity.check(deps)] == ["far_legs_held"]


def test_a_credit_diagonals_expired_stop_leads_to_the_same_alert(deps, factory):
    """The resting stop on the near short ends when the broker expires it."""
    bot_id = make_calendar(factory, live=True, near=1, far=3, offset="-100", credit=True)
    bot_entry.fire(deps, bot_id, "09:47")
    (stop,) = exits(factory, "stop")
    at(deps, ny(14, 16, 1))  # the near put (2026-10-14) expired
    old = deps.tasty.orders[stop.broker_order_id]
    deps.tasty.orders[stop.broker_order_id] = replace(old, status=OrderStatus.EXPIRED)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "expired"
    bot_settle.settle_pass(deps)
    (settle,) = exits(factory, "settle")
    assert settle.state == "left"
    assert [leg["symbol"] for leg in settle.legs] == [sym(5590, "put", "2026-10-16")]


def test_settle_still_says_so_while_frozen(deps, factory):
    """It sends nothing, so Freeze doesn't hold the alert back."""
    calendar(deps, factory)
    orders.set_switch(factory, "freeze", True)
    at(deps, EXPIRY_EVENING)
    bot_settle.settle_pass(deps)
    assert exits(factory, "settle")[0].state == "left" and sells(deps) == []


def test_a_group_whose_stop_already_sold_its_far_leg_is_not_settled(deps, factory):
    """A credit diagonal stopped out during the day: its far leg went with the stop (the
    group's remainder), so nothing is left, and nothing is said."""
    bot_id = make_calendar(factory, live=True, near=1, far=3, offset="-100", credit=True)
    bot_entry.fire(deps, bot_id, "09:47")
    (stop,) = exits(factory, "stop")
    deps.hub.set(sym(5590, "put", "2026-10-16"), D("1.40"), D("1.60"), OPEN_TIME)
    deps.tasty.fill(stop.broker_order_id, D("4.50"))
    bot_exits.monitor_pass(deps)  # the far put sold at market
    assert [w.state for w in exits(factory, "wing")] in (["filled"], ["working"])
    deps.seen.clear()
    at(deps, ny(15, 10, 0))  # the day after the near put expired
    bot_settle.settle_pass(deps)
    assert exits(factory, "settle") == []
    assert not [e for e in deps.seen if "near legs expired" in e.title]


def test_a_group_its_target_closed_is_not_settled(deps, factory):
    calendar(deps, factory, target="25")
    with factory() as session:
        target = session.scalars(select(BotExit).where(BotExit.kind == "target")).one()
        target.state = "filled"
        session.commit()
    at(deps, AFTER_THE_PAUSE)
    bot_settle.settle_pass(deps)
    assert exits(factory, "settle") == []
    at(deps, AFTER_THE_PAUSE + timedelta(hours=1))
    assert [f.check for f in bot_integrity.check(deps)] == []


def test_a_watched_stop_that_closed_the_group_before_the_expiry_leaves_nothing(deps, factory):
    """Review 4: the calendar's watched stop fired and closed it the day before; when the
    near leg expires there's no settle row and nothing is sent."""
    calendar(deps, factory)
    (stop,) = exits(factory, "stop")
    near, far = (leg.symbol for leg in bot_exits.exit_legs(stop))
    for minute in (0, 1):
        moment = at(deps, ny(19, 10, minute))
        deps.hub.set(near, D("0.90"), D("1.10"), moment)  # P&L −2.00, past −1.75
        deps.hub.set(far, D("2.40"), D("2.60"), moment)
        from optionsjourney.services import bot_watched_stops

        bot_watched_stops.watch_pass(deps)
    (close,) = exits(factory, "close")
    assert close.state == "filled"
    sent = len(deps.tasty.calls)
    at(deps, EXPIRY_EVENING)
    bot_exits.monitor_pass(deps)
    bot_settle.settle_pass(deps)
    at(deps, AFTER_THE_PAUSE)
    bot_settle.settle_pass(deps)
    assert exits(factory, "settle") == [] and len(deps.tasty.calls) == sent
    assert [f.check for f in bot_integrity.check(deps)] == []


def test_a_sold_far_leg_is_named(deps, factory):
    """A group whose far leg is sold (refused by validation, written straight to the
    tables) is named in the alert; nothing is ever sent."""
    _, run_id = calendar(deps, factory)
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.legs = [dict(leg, side="buy" if leg["side"] == "sell" else "sell") for leg in run.legs]
        session.commit()
    at(deps, AFTER_THE_PAUSE)
    bot_settle.settle_pass(deps)
    (settle,) = exits(factory, "settle")
    assert settle.state == "left" and sells(deps) == []
    [said] = [e for e in deps.seen if "near legs expired" in e.title]
    assert "is sold" in said.detail and "Check your broker" in said.detail


def test_a_double_calendar_in_one_group_names_both_far_legs(deps, factory):
    bot_id = make_calendar(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    legs = (
        *bot.legs,
        LegDef(2, Side.SELL, "call", dte=7, target=D("6.40")),
        LegDef(3, Side.BUY, "call", dte=14, pick="offset", target=D("0"), link=2),
    )
    bot = replace(bot, legs=legs, groups=(replace(bot.groups[0], name="Double"),))
    assert bot_store.save(factory, bot, OPEN_TIME)[1] == {}
    bot_entry.fire(deps, bot_id, "09:47")
    at(deps, AFTER_THE_PAUSE)
    bot_settle.settle_pass(deps)
    (settle,) = exits(factory, "settle")
    assert sorted(leg["symbol"] for leg in settle.legs) == sorted(
        [sym(5690, "put", FAR), sym(5910, "call", FAR)]
    )
    assert sells(deps) == []
