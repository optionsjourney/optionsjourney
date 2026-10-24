import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.brokers.http import BrokerError
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import BotRun, OrderLog, Setting
from optionsjourney.services import orders, trade_orders, trade_ticket
from optionsjourney.trading.check import check_cancel
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, Leg, Order, OrderStatus, Side
from tests.services.test_orders import FakeBroker, make_bot
from tests.services.test_trade_ticket import BODY

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
PUT = "SPXW  261013P05800000"
OUTSIDE = "Placed outside the app. Change or cancel it at your broker."


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


def test_replace_an_order_placed_outside_the_app_is_refused(factory):
    tasty = FakeBroker(held={})
    tasty.working = [_working("900")]
    replaced = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "900", 1.6)
    assert replaced.action == "refuse" and replaced.message == OUTSIDE
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
    bot_id = make_bot(factory)
    with factory() as session:
        mine = session.scalars(select(OrderLog).where(OrderLog.broker_order_id == "777")).first()
        cols = {c.name: getattr(mine, c.name) for c in OrderLog.__table__.columns}
        cols.update(id=None, origin="bot", bot_id=bot_id)
        session.add(OrderLog(**cols))
        session.commit()
    tasty.working = [_working("777")]
    assert trade_orders.working(factory, [tasty], "tastytrade", "5WX01234")[0]["yours"] is False
    replaced = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", 1.6)
    cancelled = trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "777")
    for result in (replaced, cancelled):
        assert result.action == "refuse"
        assert result.message == "Managed by SPX put spread, use its controls."
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


# --- Owners: only your orders can be changed (#292, #293) ---------------------------------

FLY = ("SPXW  261023P05800000", "SPXW  261023P05750000", "SPXW  261023P05700000")


def _outside(order_id="900", status=OrderStatus.WORKING, price="1.20", **terms):
    """A long put butterfly bought 2 times at the broker: legs 2 / 4 / 2."""
    legs = (
        BrokerLeg(FLY[0], Side.BUY, True, 2, 2),
        BrokerLeg(FLY[1], Side.SELL, True, 4, 4),
        BrokerLeg(FLY[2], Side.BUY, True, 2, 2),
    )
    price = Decimal(price) if price is not None else None
    return BrokerOrder(order_id, status, legs, entered_at=NOW, price=price, **terms)


def _log(factory, order_id, origin, bot_id=None, bot_run_id=None, action="send", ok=True,
         result="Sent.", tag=None, order=None, created_at=NOW, broker="tastytrade"):  # fmt: skip
    with factory() as session:
        session.add(OrderLog(
            created_at=created_at, broker=broker, account_number="5WX01234", origin=origin,
            bot_id=bot_id, action=action, opening=True, quantity=1, price=Decimal("1.50"),
            order_type="limit", order={"order": order} if order else {}, result=result, ok=ok,
            broker_order_id=order_id, bot_run_id=bot_run_id, tag=tag,
        ))  # fmt: skip
        session.commit()


def _fly_order():
    legs = (Leg(FLY[0], Side.BUY, 1), Leg(FLY[1], Side.SELL, 2), Leg(FLY[2], Side.BUY, 1))
    return Order(legs=legs, quantity=2, opening=True, price=Decimal("-1.20")).to_dict()


def _run(factory, bot_id, order_id):
    with factory() as session:
        run = BotRun(bot_id=bot_id, key=f"run-{order_id}", trading_date=date(2026, 10, 23),
                     entry_time="10:00", status="filled", live=True, broker_order_id=order_id,
                     created_at=NOW, updated_at=NOW)  # fmt: skip
        session.add(run)
        session.commit()
        return run.id


def _no_actions(factory, tasty, reason, broker="tastytrade", order_id="900"):
    row = trade_orders.working(factory, [tasty], broker, "5WX01234", now=NOW)[0]
    assert row["can_change"] is False and row["can_cancel"] is False
    assert row["reason"] == reason
    for result in (
        trade_orders.cancel(factory, [tasty], broker, "5WX01234", order_id, now=NOW),
        trade_orders.replace(factory, [tasty], broker, "5WX01234", order_id, 1.3, now=NOW),
    ):
        assert result.action == "refuse" and result.message == reason
    assert not [c for c in tasty.calls if c[0] in ("replace", "cancel")]
    return row


def test_owner_kinds(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)  # 777: yours
    bot_id = make_bot(factory)
    run_id = _run(factory, bot_id, "other")
    _log(factory, "801", "bot", bot_id=bot_id, bot_run_id=run_id)
    _log(factory, "802", "managed")
    _run(factory, bot_id, "803")  # adopted: a run, no log row
    got = {
        order_id: trade_orders.owner(factory, "tastytrade", "5WX01234", _outside(order_id))
        for order_id in ("777", "801", "802", "803", "804")
    }
    assert got == {
        "777": trade_orders.Owner("you", None, None),
        "801": trade_orders.Owner("bot", "SPX put spread", f"/automation/runs/{run_id}"),
        "802": trade_orders.Owner("managed", None, None),
        "803": trade_orders.Owner("bot", "SPX put spread", None),
        "804": trade_orders.Owner("outside", None, None),
    }


def test_the_outside_refusal_has_its_own_words():
    assert trade_orders.OUTSIDE == OUTSIDE
    assert trade_orders.Owner("outside").refusal == OUTSIDE
    assert not trade_orders.Owner("outside").changeable


def test_an_outside_order_is_listed_read_only(factory):
    tasty = FakeBroker(held={})
    tasty.working = [_outside()]
    row = _no_actions(factory, tasty, OUTSIDE)
    assert row["owner"] == {"kind": "outside", "name": None, "link": None}
    assert row["yours"] is False and row["price"] == 1.2
    assert row["category"] == "options"  # expires 23 Oct, listed on 13 Oct


def test_working_your_order_keeps_its_actions(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    tasty.working = [_working("777")]
    row = trade_orders.working(factory, [tasty], "tastytrade", "5WX01234")[0]
    assert row["owner"]["kind"] == "you" and row["yours"] is True
    assert row["can_change"] is True and row["can_cancel"] is True and row["reason"] is None


def test_working_bot_order_has_no_actions(factory):
    tasty = FakeBroker(held={})
    bot_id = make_bot(factory)
    _log(factory, "801", "bot", bot_id=bot_id)
    _log(factory, "802", "managed")
    tasty.working = [_outside("801"), _outside("802")]
    bot, managed = trade_orders.working(factory, [tasty], "tastytrade", "5WX01234")
    for row in (bot, managed):
        assert row["can_change"] is False and row["can_cancel"] is False
    assert bot["reason"] == "Managed by SPX put spread, use its controls."
    assert managed["reason"] == "Managed by this trade's rules, use its controls."
    assert bot["owner"] == {"kind": "bot", "name": "SPX put spread", "link": None}


@pytest.mark.parametrize(
    ("found", "error", "says"),
    [
        (
            BrokerOrder("777", OrderStatus.FILLED, (BrokerLeg(PUT, Side.SELL, True, 1, 0),)),
            None,
            trade_orders.NOT_WORKING,
        ),
        (dataclasses.replace(_working("777"), changing=True), None, trade_orders.CHANGING),
        (_working("777"), BrokerError("TastyTrade answered 500"), trade_orders.COULDNT_READ),
    ],
)
def test_cancel_your_order_reads_it_fresh_first(factory, found, error, says):
    """Even an order the log says is yours is read from the broker before a cancel."""
    tasty = FakeBroker(held={}, errors={"get_order": error} if error else None)
    _sent(factory, tasty)
    tasty.working = [found]
    result = trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "777")
    assert result.action == "refuse" and result.message == says
    assert not [c for c in tasty.calls if c[0] == "cancel"]


def test_the_owner_is_read_again_at_cancel_and_replace(factory):
    """Listed as outside, then a bot run adopts it: the refusal names the bot."""
    tasty = FakeBroker(held={})
    tasty.working = [_outside()]
    row = trade_orders.working(factory, [tasty], "tastytrade", "5WX01234")[0]
    assert row["owner"]["kind"] == "outside" and row["reason"] == OUTSIDE
    _run(factory, make_bot(factory), "900")
    for result in (
        trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "900"),
        trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "900", 1.3),
    ):
        assert result.action == "refuse"
        assert result.message == "Managed by SPX put spread, use its controls."
    assert not [c for c in tasty.calls if c[0] in ("replace", "cancel")]


def test_freeze_still_refuses_your_cancel_and_replace(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    tasty.working = [_working("777")]
    orders.set_switch(factory, "freeze", True)
    cancelled = trade_orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "777")
    replaced = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "777", 1.6)
    for result in (cancelled, replaced):
        assert result.action == "refuse" and not result.ok
        assert result.message == check_cancel(orders.Switches(freeze=True)).reason
    assert not [c for c in tasty.calls if c[0] in ("replace", "cancel")]


def test_your_order_found_by_its_tag_can_be_replaced(factory):
    """TastyTrade: accepted with no order id, but the tag is your attempt."""
    tasty = FakeBroker(held={})
    tasty.working = [dataclasses.replace(_outside(price="-1.20"), tag="a1b2c3d4e5f6a7b8c9d0")]
    _log(factory, None, "you", tag="a1b2c3d4e5f6a7b8c9d0", order=_fly_order())
    row = trade_orders.working(factory, [tasty], "tastytrade", "5WX01234", now=NOW)[0]
    assert row["owner"]["kind"] == "you" and row["can_change"] and row["can_cancel"]
    result = trade_orders.replace(factory, [tasty], "tastytrade", "5WX01234", "900", -1.3)
    assert result.action == "replace" and result.ok
    with factory() as session:
        sent = session.scalars(select(OrderLog).where(OrderLog.action == "replace")).one()
    assert sent.order["order"]["quantity"] == 2 and Decimal(sent.order["order"]["price"]) == (
        Decimal("-1.3")
    )


def test_a_tag_match_makes_it_the_bots(factory):
    """TastyTrade: accepted with no order id, but the tag is the bot's attempt."""
    tasty = FakeBroker(held={})
    tasty.working = [dataclasses.replace(_outside(), tag="a1b2c3d4e5f6a7b8c9d0")]
    bot_id = make_bot(factory)
    _log(factory, None, "bot", bot_id=bot_id, tag="a1b2c3d4e5f6a7b8c9d0", order=_fly_order())
    row = _no_actions(factory, tasty, "Managed by SPX put spread, use its controls.")
    assert row["owner"] == {"kind": "bot", "name": "SPX put spread", "link": None}


def test_schwab_order_with_the_apps_tag_is_unknown(factory):
    schwab = FakeBroker(broker="schwab", held={})
    schwab.working = [dataclasses.replace(_outside(), tag="TA_ourapp")]
    with factory() as session:
        session.add(Setting(key=orders.SCHWAB_TAG_KEY.format("5WX01234"), value="TA_ourapp",
                            updated_at=NOW))  # fmt: skip
        session.commit()
    row = _no_actions(factory, schwab, trade_orders.UNKNOWN_PART, broker="schwab")
    assert row["owner"]["kind"] == "unknown"
    schwab.working = [dataclasses.replace(_outside(), tag="TA_another_app")]
    row = trade_orders.working(factory, [schwab], "schwab", "5WX01234", now=NOW)[0]
    assert row["owner"]["kind"] == "outside" and row["reason"] == OUTSIDE
    assert not row["can_cancel"]


def test_your_order_the_broker_is_already_changing_has_no_actions(factory):
    tasty = FakeBroker(held={})
    _sent(factory, tasty)
    tasty.working = [dataclasses.replace(_working("777"), changing=True)]
    _no_actions(factory, tasty, trade_orders.CHANGING, order_id="777")


def test_a_tastytrade_order_with_an_app_style_tag_and_no_row_is_unknown(factory):
    """The broker took it but the app never logged it (a crash in between)."""
    tasty = FakeBroker(held={})
    tasty.working = [dataclasses.replace(_outside(), tag="0123456789abcdef0123")]
    row = _no_actions(factory, tasty, trade_orders.UNKNOWN_PART)
    assert row["owner"]["kind"] == "unknown"
    for tag in ("0123456789ABCDEF0123", "0123456789abcdef012", "my-own-tag"):
        tasty.working = [dataclasses.replace(_outside(), tag=tag)]
        row = trade_orders.working(factory, [tasty], "tastytrade", "5WX01234", now=NOW)[0]
        assert row["owner"]["kind"] == "outside" and row["reason"] == OUTSIDE
