from decimal import Decimal

import pytest

from optionsjourney.trading.orders import (
    BrokerLeg,
    BrokerOrder,
    Leg,
    Order,
    OrderError,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
    order_effect,
    plain,
    same_legs,
)

SHORT = Leg("SPXW  261013P05800000", Side.SELL)
LONG = Leg("SPXW  261013P05790000", Side.BUY)


def spread(**changes):
    values = {"legs": (SHORT, LONG), "quantity": 2, "opening": True, "price": Decimal("1.50")}
    values.update(changes)
    return Order(**values)


def test_a_credit_spread():
    order = spread()
    assert order.order_type is OrderType.LIMIT and order.time_in_force is TimeInForce.DAY
    assert order.contracts == {SHORT.symbol: 2, LONG.symbol: 2}


def test_leg_roots_and_futures():
    assert SHORT.root == "SPXW" and not SHORT.futures
    future = Leg("./ESZ6 EW2Z6 261218P7000", "sell")
    assert future.futures and future.root == "/ESZ6" and future.side is Side.SELL


def test_ratio_multiplies_contracts():
    body = Leg("SPXW  261013P05785000", Side.SELL, ratio=2)
    wing = Leg("SPXW  261013P05780000", Side.BUY)
    order = Order((LONG, body, wing), quantity=3, opening=True, price=Decimal("-0.40"))
    assert order.contracts[body.symbol] == 6


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"legs": ()}, "at least one leg"),
        ({"quantity": 0}, "at least 1 unit"),
        ({"legs": (SHORT, SHORT)}, "appears twice"),
        ({"price": None}, "needs a price"),
        ({"price": Decimal("0")}, "can't be zero"),
        ({"order_type": OrderType.MARKET}, "has no limit price"),
        ({"order_type": OrderType.STOP, "price": None, "trigger": Decimal("3")}, "one leg"),
        ({"trigger": Decimal("3")}, "Only stop orders"),
        ({"time_in_force": TimeInForce.GTC_OVERNIGHT, "order_type": OrderType.MARKET,
          "price": None}, "limit orders"),
        ({"legs": (SHORT, Leg("./ESZ6 EW2Z6 261218P7000", Side.BUY))}, "can't be in one order"),
    ],
)  # fmt: skip
def test_invalid_orders_are_refused(changes, message):
    with pytest.raises(OrderError, match=message):
        spread(**changes)


def test_stop_orders_need_a_trigger_above_zero():
    with pytest.raises(OrderError, match="trigger price above zero"):
        Order((SHORT,), 1, False, OrderType.STOP)
    stop = Order((Leg(SHORT.symbol, Side.BUY),), 1, False, OrderType.STOP, trigger=Decimal("4.5"))
    assert stop.price is None and stop.trigger == Decimal("4.5")


def test_bad_side_or_ratio():
    with pytest.raises(OrderError, match="buy or sell"):
        Leg(SHORT.symbol, "hold")
    with pytest.raises(OrderError, match="at least 1 contract"):
        Leg(SHORT.symbol, Side.BUY, ratio=0)


def test_round_trip_through_a_dict():
    order = spread(time_in_force=TimeInForce.GTC)
    assert Order.from_dict(order.to_dict()) == order
    assert order.to_dict()["price"] == "1.50"


def test_same_legs_ignores_padding_and_order():
    found = BrokerOrder(
        "9",
        OrderStatus.WORKING,
        (
            BrokerLeg(plain(LONG.symbol), Side.BUY, True, 2, 2),
            BrokerLeg(plain(SHORT.symbol), Side.SELL, True, 2, 2),
        ),
    )
    assert found.working
    assert same_legs(spread(), found)
    assert not same_legs(spread(quantity=1), found)
    assert not same_legs(spread(opening=False), found)


@pytest.mark.parametrize("price", ["abc", "NaN", "Infinity", "-Infinity", float("inf")])
def test_a_price_must_be_a_finite_number(price):
    with pytest.raises(OrderError, match="isn't a valid"):
        spread(price=price)


@pytest.mark.parametrize("quantity", [True, 1.5, 2.0, "2"])
def test_the_quantity_must_be_a_whole_number(quantity):
    with pytest.raises(OrderError, match="whole number"):
        spread(quantity=quantity)


@pytest.mark.parametrize("ratio", [True, 2.0, "2"])
def test_a_ratio_must_be_a_whole_number(ratio):
    with pytest.raises(OrderError, match="whole number"):
        Leg(SHORT.symbol, Side.SELL, ratio=ratio)


def test_an_unpadded_occ_symbol_is_padded():
    assert Leg("SPXW261013P05800000", Side.SELL).symbol == SHORT.symbol
    assert Leg(" SPY 261013P00580000 ", Side.SELL).symbol == "SPY   261013P00580000"


@pytest.mark.parametrize(
    "symbol", ["SPXW", "SPXW  2610", "SPXW  261013X05800000", "./ESZ6", "./ESZ6 EW2Z6", 5]
)
def test_a_symbol_must_be_an_occ_or_futures_option_symbol(symbol):
    with pytest.raises(OrderError, match="isn't an option symbol"):
        Leg(symbol, Side.SELL)


def test_a_single_legs_price_sign_must_match_its_side():
    with pytest.raises(OrderError, match="credit"):
        Order((Leg(SHORT.symbol, Side.SELL),), 1, True, price=Decimal("-1.00"))
    with pytest.raises(OrderError, match="debit"):
        Order((Leg(SHORT.symbol, Side.BUY),), 1, False, price=Decimal("1.00"))
    assert Order((Leg(SHORT.symbol, Side.BUY),), 1, False, price=Decimal("-1.00")).price < 0


PUT = "SPXW  261016P07700000"
WING = "SPXW  261016P07650000"


def test_nothing_held_is_opening():
    assert order_effect([(PUT, Side.SELL), (WING, Side.BUY)], {}) == "opening"


def test_buying_back_a_short_and_selling_a_long_is_closing():
    held = {plain(PUT): -1, plain(WING): 1}
    assert order_effect([(PUT, Side.BUY), (WING, Side.SELL)], held) == "closing"


def test_adding_to_a_position_is_opening():
    assert order_effect([(PUT, Side.SELL)], {plain(PUT): -1}) == "opening"


def test_closing_more_than_held_is_still_closing():
    assert order_effect([(PUT, Side.BUY)], {plain(PUT): -1}) == "closing"


def test_mixed_legs_are_a_roll():
    held = {plain(PUT): -1}
    assert order_effect([(PUT, Side.BUY), (WING, Side.SELL)], held) == "roll"
