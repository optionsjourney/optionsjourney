from datetime import UTC, datetime
from decimal import Decimal

import pytest

from optionsjourney.trading.orders import (
    Leg,
    Order,
    OrderError,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from optionsjourney.trading.schwab import broker_order, order_body, strategy_type

D = Decimal
P5800 = "SPXW  261013P05800000"
P5790 = "SPXW  261013P05790000"
C5900 = "SPXW  261013C05900000"
C5910 = "SPXW  261013C05910000"


def test_a_credit_vertical():
    order = Order((Leg(P5800, Side.SELL), Leg(P5790, Side.BUY)), 2, True, price=D("1.50"))
    assert order_body(order) == {
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": "VERTICAL",
        "orderType": "NET_CREDIT",
        "price": "1.50",
        "orderLegCollection": [
            {"instruction": "SELL_TO_OPEN", "quantity": 2,
             "instrument": {"symbol": P5800, "assetType": "OPTION"}},
            {"instruction": "BUY_TO_OPEN", "quantity": 2,
             "instrument": {"symbol": P5790, "assetType": "OPTION"}},
        ],
    }  # fmt: skip


def test_a_debit_close_and_a_single_leg_stop():
    close = Order(
        (Leg(P5800, Side.BUY), Leg(P5790, Side.SELL)), 1, False, price=D("-0.35"),
        time_in_force=TimeInForce.GTC,
    )  # fmt: skip
    body = order_body(close)
    assert (body["orderType"], body["price"], body["duration"]) == (
        "NET_DEBIT",
        "0.35",
        "GOOD_TILL_CANCEL",
    )
    stop = Order((Leg(P5800, Side.BUY),), 1, False, OrderType.STOP, trigger=D("4.5"))
    body = order_body(stop)
    assert body["orderType"] == "STOP" and body["stopPrice"] == "4.50" and "price" not in body
    assert body["complexOrderStrategyType"] == "NONE"


def test_a_bots_resting_stop_and_how_schwab_reads_it_back():
    """Decision 0060: the stop a bot rests at Schwab is one leg, GTC, a stop market order;
    Schwab says a stop not yet triggered is AWAITING_STOP_CONDITION, which is working. A
    stop of more than one leg is never made."""
    stop = Order((Leg(P5800, Side.BUY),), 2, False, OrderType.STOP, trigger=D("4.8"),
                 time_in_force=TimeInForce.GTC)  # fmt: skip
    body = order_body(stop)
    assert (body["orderType"], body["stopPrice"], body["duration"]) == (
        "STOP",
        "4.80",
        "GOOD_TILL_CANCEL",
    )
    assert body["orderLegCollection"] == [
        {"instruction": "BUY_TO_CLOSE", "quantity": 2,
         "instrument": {"symbol": P5800, "assetType": "OPTION"}},
    ]  # fmt: skip
    raw = {"orderId": 7, "status": "AWAITING_STOP_CONDITION", "quantity": 2,
           "filledQuantity": 0, "orderLegCollection": body["orderLegCollection"]}  # fmt: skip
    found = broker_order(raw)
    assert found.status is OrderStatus.WORKING and found.may_be_working
    with pytest.raises(OrderError, match="one leg"):
        Order((Leg(P5800, Side.BUY), Leg(P5790, Side.SELL)), 1, False, OrderType.STOP,
              trigger=D("4.8"))  # fmt: skip


def test_strategy_types():
    condor = Order(
        (Leg("SPXW  261013P05780000", Side.BUY), Leg(P5800, Side.SELL),
         Leg(C5900, Side.SELL), Leg(C5910, Side.BUY)), 1, True, price=D("2"),
    )  # fmt: skip
    assert strategy_type(condor) == "IRON_CONDOR"
    fly = Order(
        (Leg(P5790, Side.BUY), Leg(P5800, Side.SELL, 2), Leg("SPXW  261013P05810000", Side.BUY)),
        1, True, price=D("-0.5"),
    )  # fmt: skip
    assert strategy_type(fly) == "BUTTERFLY"
    calendar = Order(
        (Leg(P5800, Side.SELL), Leg("SPXW  261020P05800000", Side.BUY)), 1, True, price=D("-1")
    )
    assert strategy_type(calendar) == "CALENDAR"


def test_schwab_refuses_futures_and_overnight():
    with pytest.raises(OrderError, match="futures"):
        order_body(Order((Leg("./ESZ6 EW2Z6 261218P7000", Side.SELL),), 1, True, price=D("5")))
    overnight = Order((Leg(P5800, Side.SELL),), 1, True, price=D("2"),
                      time_in_force=TimeInForce.GTC_OVERNIGHT)  # fmt: skip
    with pytest.raises(OrderError, match="overnight"):
        order_body(overnight)


def test_reading_an_order_back():
    raw = {
        "orderId": 1001,
        "status": "WORKING",
        "tag": "TA_appname",
        "enteredTime": "2026-10-13T14:00:00+0000",
        "quantity": 2,
        "filledQuantity": 1,
        "orderLegCollection": [
            {"instruction": "BUY_TO_CLOSE", "quantity": 2, "instrument": {"symbol": P5800}},
        ],
    }
    found = broker_order(raw)
    assert found.broker_order_id == "1001" and found.tag == "TA_appname"
    assert found.status is OrderStatus.PARTIAL
    assert (found.legs[0].quantity, found.legs[0].remaining) == (2, 1)
    assert found.legs[0].side is Side.BUY and not found.legs[0].opening
    assert found.entered_at == datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    assert broker_order({"orderId": 2, "status": "CANCELED"}).status is OrderStatus.CANCELLED
    assert broker_order({"orderId": 3, "status": "FILLED"}).status is OrderStatus.FILLED


def test_entered_times_with_fractions_or_garbage():
    from optionsjourney.trading.schwab import _time

    assert _time("2026-10-13T14:00:00.123+0000").microsecond == 123000
    assert _time("2026-10-13T14:00:00+0000").hour == 14
    assert _time("yesterday") is None


def test_leg_fill_price_from_execution_legs():
    raw = {
        "orderId": 5, "status": "FILLED", "quantity": 1, "filledQuantity": 1,
        "orderLegCollection": [
            {"legId": 1, "instruction": "SELL_TO_OPEN", "quantity": 1,
             "instrument": {"symbol": P5800}},
        ],
        "orderActivityCollection": [
            {"executionLegs": [{"legId": 1, "price": 2.9, "quantity": 1}]}
        ],
    }  # fmt: skip
    assert broker_order(raw).legs[0].fill_price == D("2.9")


def test_executions_with_no_price_are_skipped():
    def order(executions):
        return {
            "orderId": 5, "status": "FILLED", "quantity": 2, "filledQuantity": 2,
            "orderLegCollection": [
                {"legId": 1, "instruction": "SELL_TO_OPEN", "quantity": 2,
                 "instrument": {"symbol": P5800}},
            ],
            "orderActivityCollection": [{"executionLegs": executions}],
        }  # fmt: skip

    priced = [{"legId": 1, "price": 2.9, "quantity": 1}, {"legId": 1, "quantity": 1}]
    assert broker_order(order(priced)).legs[0].fill_price == D("2.9")
    assert broker_order(order([{"legId": 1, "quantity": 2}])).legs[0].fill_price is None
