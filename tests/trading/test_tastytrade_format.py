from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.trading.orders import (
    Leg,
    Order,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from optionsjourney.trading.tastytrade import broker_order, order_body

SHORT = Leg("SPXW  261013P05800000", Side.SELL)
LONG = Leg("SPXW  261013P05790000", Side.BUY)


def test_a_credit_spread_to_open():
    order = Order((SHORT, LONG), 2, True, price=Decimal("1.50"))
    assert order_body(order, tag="oj-abc") == {
        "time-in-force": "Day",
        "order-type": "Limit",
        "price": "1.50",
        "price-effect": "Credit",
        "external-identifier": "oj-abc",
        "legs": [
            {"instrument-type": "Equity Option", "symbol": SHORT.symbol, "quantity": 2,
             "action": "Sell to Open"},
            {"instrument-type": "Equity Option", "symbol": LONG.symbol, "quantity": 2,
             "action": "Buy to Open"},
        ],
    }  # fmt: skip


def test_a_debit_to_close_good_till_cancelled():
    order = Order(
        (Leg(SHORT.symbol, Side.BUY), Leg(LONG.symbol, Side.SELL)),
        1,
        False,
        price=Decimal("-0.35"),
        time_in_force=TimeInForce.GTC,
    )
    body = order_body(order)
    assert body["price"] == "0.35" and body["price-effect"] == "Debit"
    assert body["time-in-force"] == "GTC" and "external-identifier" not in body
    assert [leg["action"] for leg in body["legs"]] == ["Buy to Close", "Sell to Close"]


def test_a_stop_and_a_futures_option():
    stop = Order((Leg(SHORT.symbol, Side.BUY),), 1, False, OrderType.STOP, trigger=Decimal("4.5"))
    body = order_body(stop)
    assert body["order-type"] == "Stop" and body["stop-trigger"] == "4.5"
    assert "price" not in body
    fop = Order((Leg("./ESZ6 EW2Z6 261218P7000", Side.SELL),), 1, True, price=Decimal("12.25"))
    assert order_body(fop)["legs"][0]["instrument-type"] == "Future Option"
    overnight = Order(
        (SHORT,), 1, True, price=Decimal("2"), time_in_force=TimeInForce.GTC_OVERNIGHT
    )
    assert order_body(overnight)["time-in-force"] == "GTC Ext Overnight"


def test_reading_an_order_back():
    raw = {
        "id": 777,
        "status": "Live",
        "external-identifier": "oj-abc",
        "received-at": "2026-10-13T14:00:01.123+00:00",
        "legs": [
            {"symbol": SHORT.symbol, "action": "Buy to Close", "quantity": "2",
             "remaining-quantity": "1"},
        ],
    }  # fmt: skip
    found = broker_order(raw)
    assert found.broker_order_id == "777" and found.tag == "oj-abc"
    assert found.status is OrderStatus.PARTIAL and found.working
    assert found.legs[0].side is Side.BUY and not found.legs[0].opening
    assert (found.legs[0].quantity, found.legs[0].remaining) == (2, 1)
    assert found.entered_at == datetime(2026, 10, 13, 14, 0, 1, 123000, tzinfo=UTC)


def test_statuses():
    def status(text):
        return broker_order({"id": 1, "status": text, "legs": []}).status

    assert status("Received") is OrderStatus.WORKING
    assert status("Filled") is OrderStatus.FILLED
    assert status("Cancelled") is OrderStatus.CANCELLED
    assert status("Rejected") is OrderStatus.REJECTED
    assert status("Expired") is OrderStatus.EXPIRED
    assert status("Something new") is OrderStatus.UNKNOWN


def test_leg_fill_price_is_the_average_of_its_fills():
    raw = {"id": 9, "status": "Filled", "legs": [
        {"symbol": SHORT.symbol, "action": "Sell to Open", "quantity": "2",
         "remaining-quantity": "0",
         "fills": [{"fill-price": "2.70", "quantity": "1"},
                   {"fill-price": "2.80", "quantity": "1"}]},
    ]}  # fmt: skip
    assert broker_order(raw).legs[0].fill_price == Decimal("2.75")
    unfilled = {"id": 1, "status": "Live", "legs": [
        {"symbol": SHORT.symbol, "action": "Sell to Open", "quantity": "1"}]}  # fmt: skip
    assert broker_order(unfilled).legs[0].fill_price is None


def test_fills_with_no_price_are_skipped():
    raw = {"id": 9, "status": "Filled", "legs": [
        {"symbol": SHORT.symbol, "action": "Sell to Open", "quantity": "2",
         "remaining-quantity": "0",
         "fills": [{"fill-price": "2.70", "quantity": "1"}, {"quantity": "1"}]},
    ]}  # fmt: skip
    assert broker_order(raw).legs[0].fill_price == Decimal("2.70")
    no_prices = {"id": 9, "status": "Filled", "legs": [
        {"symbol": SHORT.symbol, "action": "Sell to Open", "quantity": "1",
         "remaining-quantity": "0", "fills": [{"fill-price": None, "quantity": "1"}]},
    ]}  # fmt: skip
    assert broker_order(no_prices).legs[0].fill_price is None


def test_a_replaced_order_names_its_replacement():
    """Bots slice 21 (Tracker2's replacing_order_id): an order edited at TastyTrade reads
    Cancelled and names the order that replaced it."""
    raw = {"id": 41, "status": "Cancelled", "replacing-order-id": 42, "legs": []}
    assert broker_order(raw).replaced_by == "42"
    assert broker_order({"id": 41, "status": "Cancelled", "legs": []}).replaced_by is None


def _raw(**extra):
    leg = {"symbol": "SPXW  261016P05800000", "action": "Sell to Open", "quantity": 1,
           "instrument-type": "Equity Option"}  # fmt: skip
    return {"id": 1, "status": "Live", "legs": [leg], **extra}


def test_broker_order_reads_the_signed_price():
    found = broker_order(_raw(**{"price": "1.25", "price-effect": "Credit"}))
    assert found.price == Decimal("1.25")
    debit = broker_order(_raw(price="0.80", **{"price-effect": "Debit"}))
    assert debit.price == Decimal("-0.80")
    assert broker_order(_raw()).price is None


def test_broker_order_flags_a_change_already_under_way():
    assert broker_order(_raw()).changing is False
    assert broker_order(_raw(status="Cancel Requested")).changing is True
    assert broker_order(_raw(status="Replace Requested")).changing is True


def test_broker_order_price_without_a_known_effect_is_unknown():
    assert broker_order(_raw(price="0.80")).price is None
    assert broker_order(_raw(price="0.80", **{"price-effect": "None"})).price is None
