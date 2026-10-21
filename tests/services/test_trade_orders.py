from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from optionsjourney.brokers.http import BrokerError
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import OrderLog
from optionsjourney.services import orders, trade_orders, trade_ticket
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side
from tests.services.test_orders import FakeBroker
from tests.services.test_trade_ticket import BODY

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
PUT = "SPXW  261013P05800000"


@pytest.fixture(autouse=True)
def _no_leftover_codes():
    trade_ticket._PENDING.clear()
    yield
    trade_ticket._PENDING.clear()


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def _working(order_id):
    return BrokerOrder(order_id, OrderStatus.WORKING,
                       (BrokerLeg(PUT, Side.SELL, True, 1, 1),), entered_at=NOW)  # fmt: skip


def _sent(factory, tasty):
    code = trade_ticket.review(factory, [tasty], "tastytrade", "5WX01234", BODY, now=NOW).code
    return trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", code, now=NOW)


def test_working_marks_your_orders(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    tasty.working = [_working("777"), _working("900")]
    rows = trade_orders.working(factory, [tasty], "tastytrade", "5WX01234")
    got = [(r["order_id"], r["yours"], r["price"]) for r in rows]
    assert got == [("777", True, 1.5), ("900", False, None)]


def test_cancel_your_order(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    tasty.working = [_working("777")]
    result = trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "777")
    assert result.ok and ("cancel", "5WX01234", "777") in tasty.calls


def test_replace_only_your_own_orders(factory):
    tasty = FakeBroker(held={})
    tasty.working = [_working("900")]
    replaced = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "900", 1.6)
    cancelled = trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "900")
    for result in (replaced, cancelled):
        assert result.action == "refuse" and "Only orders you placed" in result.message
    assert not [c for c in tasty.calls if c[0] in ("replace", "cancel")]


def test_replace_keeps_credit_or_debit(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    tasty.working = [_working("777")]
    flipped = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", -1.0)
    assert flipped.action == "refuse" and "Keep the same credit or debit" in flipped.message
    moved = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", 1.6)
    assert moved.action == "replace" and moved.ok


def test_replace_refuses_a_bad_price_without_the_broker(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    for bad in (0, float("nan"), float("inf"), "1.6", True, None):
        result = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", bad)
        assert result.action == "refuse"
    assert not [c for c in tasty.calls if c[0] == "replace"]


def test_replace_goes_out_on_tick(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    tasty.working = [_working("777")]
    assert trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", 1.62).ok
    with factory() as session:
        row = session.scalars(select(OrderLog).where(OrderLog.action == "replace")).one()
    assert float(row.price) == 1.6  # a credit rounds down, never up to 1.65


def test_a_cancelled_order_is_no_longer_yours(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    tasty.working = [_working("777")]
    trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "777")
    rows = trade_orders.working(factory, [tasty], "tastytrade", "5WX01234")
    assert rows[0]["yours"] is False


@pytest.mark.parametrize("how", ["freeze", "broker_error"])
def test_a_failed_cancel_keeps_your_order_yours(factory, how):
    tasty = FakeBroker(
        held={}, errors={"cancel": BrokerError("x")} if how == "broker_error" else None
    )
    _sent(factory, tasty)
    tasty.working = [_working("777")]
    if how == "freeze":
        orders.set_switch(factory, "freeze", True)
    cancelled = trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "777")
    assert not cancelled.ok
    orders.set_switch(factory, "freeze", False)
    assert trade_orders.working(factory, [tasty], "tastytrade", "5WX01234")[0]["yours"] is True
    assert trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", 1.6).ok


def test_a_bots_order_is_not_yours(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    with factory() as session:
        mine = session.scalars(select(OrderLog).where(OrderLog.broker_order_id == "777")).first()
        cols = {c.name: getattr(mine, c.name) for c in OrderLog.__table__.columns}
        cols.update(id=None, origin="bot", bot_id=None)
        session.add(OrderLog(**cols))
        session.commit()
    tasty.working = [_working("777")]
    assert trade_orders.working(factory, [tasty], "tastytrade", "5WX01234")[0]["yours"] is False
    replaced = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", 1.6)
    cancelled = trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "777")
    for result in (replaced, cancelled):
        assert result.action == "refuse" and "Only orders you placed" in result.message
    assert not [c for c in tasty.calls if c[0] in ("replace", "cancel")]


def _partly_filled(order_id):
    return BrokerOrder(order_id, OrderStatus.PARTIAL,
                       (BrokerLeg(PUT, Side.SELL, True, 5, 2),), entered_at=NOW)  # fmt: skip


@pytest.mark.parametrize(
    ("found", "error", "says"),
    [
        (_partly_filled("777"), None, "Part of this order filled"),
        # Working by status, but a leg has filled: still never replaced.
        (
            BrokerOrder("777", OrderStatus.WORKING, (BrokerLeg(PUT, Side.SELL, True, 5, 4),)),
            None,
            "Part of this order filled",
        ),
        (
            BrokerOrder("777", OrderStatus.FILLED, (BrokerLeg(PUT, Side.SELL, True, 1, 0),)),
            None,
            "isn't working",
        ),
        (_working("777"), BrokerError("TastyTrade answered 500"), "Couldn't read the order"),
    ],
)
def test_replace_refuses_an_order_that_is_partly_filled_gone_or_unread(factory, found, error, says):
    tasty = FakeBroker(held={}, errors={"get_order": error} if error else None)
    _sent(factory, tasty)
    tasty.working = [found]
    result = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", 1.6)
    assert result.action == "refuse" and says in result.message
    assert not [c for c in tasty.calls if c[0] == "replace"]
