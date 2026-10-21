from decimal import Decimal

import pytest

from optionsjourney.trading import ticks
from optionsjourney.trading.orders import Leg, Order, OrderError, OrderType, Side

D = Decimal


def single(symbol, price="1.00", side=Side.SELL, **kw):
    return Order((Leg(symbol, side),), 1, True, price=D(price), **kw)


def pair(a, b, price="1.00"):
    return Order((Leg(a, Side.SELL), Leg(b, Side.BUY)), 1, True, price=D(price))


SPX = "SPXW  261013P05800000"
SPX2 = "SPXW  261013P05790000"
AAPL = "AAPL  261016C00250000"
AAPL2 = "AAPL  261016C00255000"
ES = "./ESZ6 EW2Z6 261218P7000"
ES2 = "./ESZ6 EW2Z6 261218P6990"
AAPL_TABLE = ticks.table_from_rows([{"threshold": "3.0", "value": "0.05"}, {"value": "0.10"}])


@pytest.mark.parametrize(("price", "tick"), [("2.95", "0.05"), ("3.00", "0.10"), ("3.05", "0.10")])
def test_spx_single_legs_step_up_at_three(price, tick):
    assert ticks.order_tick(single(SPX, price), D(price), {}) == D(tick)


def test_spx_spreads_trade_in_nickels_at_any_price():
    assert ticks.order_tick(pair(SPX, SPX2, "7.40"), D("7.40"), {}) == D("0.05")


def test_most_options_trade_in_pennies():
    assert ticks.order_tick(single("XSP   261013P00580000"), D("4.37"), {}) == D("0.01")
    assert ticks.order_tick(single("SPY   261013P00580000"), D("4.37"), {}) == D("0.01")
    assert ticks.order_tick(pair(AAPL, AAPL2), D("1"), {"AAPL": AAPL_TABLE}) == D("0.01")


def test_learned_stock_increments_apply_to_single_legs():
    tables = {"AAPL": AAPL_TABLE}
    assert ticks.order_tick(single(AAPL, "2.50"), D("2.50"), tables) == D("0.05")
    assert ticks.order_tick(single(AAPL, "3.50"), D("3.50"), tables) == D("0.10")


def test_futures_use_series_then_contract_tables():
    tables = {
        "FOP:ES:EW2Z6": ticks.table_from_rows(
            [{"threshold": "5", "value": "0.05"}, {"value": "0.25"}]
        ),
        "FUT:/ESZ6": ticks.table_from_rows([{"value": "0.05"}]),
    }
    assert ticks.order_tick(single(ES, "7.00"), D("7.00"), tables) == D("0.25")
    assert ticks.order_tick(pair(ES, ES2, "7.00"), D("7.00"), tables) == D("0.05")
    assert ticks.order_tick(single(ES, "7.00"), D("7.00"), {}) == ticks.DEFAULT_TICK


def test_keys_for_each_kind_of_order():
    assert ticks.keys_for(single(AAPL)) == {"AAPL"}
    assert ticks.keys_for(pair(AAPL, AAPL2)) == set()
    assert ticks.keys_for(single(SPX)) == set()
    assert ticks.keys_for(single(ES)) == {"FOP:ES:EW2Z6"}
    assert ticks.keys_for(pair(ES, ES2)) == {"FOP:ES:EW2Z6", "FUT:/ESZ6"}
    assert ticks.product_of("/MESZ6") == "MES"


def test_rounding_favours_a_fill():
    assert ticks.round_price(D("4.92"), D("0.05")) == D("4.90")  # a credit asks less
    assert ticks.round_price(D("-2.33"), D("0.05")) == D("-2.35")  # a debit pays more
    assert ticks.round_trigger(D("4.62"), D("0.05")) == D("4.60")
    assert ticks.round_trigger(D("4.63"), D("0.05")) == D("4.65")
    assert ticks.on_tick(D("4.90"), D("0.05")) and not ticks.on_tick(D("4.92"), D("0.05"))


def test_rounded_order():
    order = ticks.rounded(pair(SPX, SPX2, "1.53"), {})
    assert order.price == D("1.50")
    stop = Order((Leg(SPX, Side.BUY),), 1, False, OrderType.STOP, trigger=D("3.33"))
    assert ticks.rounded(stop, {}).trigger == D("3.30")


def test_a_price_that_rounds_to_zero_raises():
    with pytest.raises(OrderError, match="can't be zero"):
        ticks.rounded(pair(SPX, SPX2, "0.03"), {})


def test_table_rows_round_trip():
    assert ticks.table_from_rows(ticks.table_to_rows(AAPL_TABLE)) == AAPL_TABLE


def test_bad_table_rows_are_skipped():
    rows = [
        {"threshold": "3.0", "value": "0"},
        {"threshold": "Infinity", "value": "0.05"},
        {"threshold": "-1", "value": "0.05"},
        {"threshold": "3.0", "value": "NaN"},
        {"threshold": "3.0", "value": "abc"},
        {"threshold": "3.0"},
        {"value": "0.10"},
    ]
    assert ticks.table_from_rows(rows) == (ticks.TickRow(None, D("0.10")),)
    assert ticks.table_from_rows([{"value": "-0.05"}, {"value": "Infinity"}]) == ()
    assert ticks.order_tick(single(AAPL, "2.50"), D("2.50"), {"AAPL": ()}) == ticks.DEFAULT_TICK


def test_a_learned_table_rounds_without_the_tables_in_hand():
    """Bots slice 16 (pricing._OPTION_TICKS): the exit math passes no tables."""
    one = Order((Leg("AAPL  261016P00200000", Side.BUY),), 1, False, price=D("-4.12"))
    try:
        assert ticks.order_tick(one, D("4.12"), {}) == D("0.01")
        ticks.learn("AAPL", ticks.table_from_rows([{"threshold": "3", "value": "0.01"},
                                                   {"value": "0.05"}]))  # fmt: skip
        assert ticks.order_tick(one, D("4.12"), {}) == D("0.05")
        assert ticks.order_tick(one, D("2.12"), {}) == D("0.01")
    finally:
        ticks.forget_learned()
