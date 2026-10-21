from datetime import UTC, date, datetime
from decimal import Decimal

from optionsjourney.core.positions import Position, PositionLeg
from optionsjourney.core.risk_graph import graph_legs, graph_symbol
from optionsjourney.core.transactions import InstrumentType, OptionDetails

D = Decimal


def _leg(symbol, put_call, strike, sign, opened, open_value, closed="0", *,
         kind=InstrumentType.OPTION, underlying="SPXW", expiration=date(2026, 11, 20)):  # fmt: skip
    return PositionLeg(
        symbol=symbol,
        underlying=underlying,
        instrument_type=kind,
        option=OptionDetails(put_call, D(strike), expiration),
        sign=sign,
        opened=D(opened),
        open_value=D(open_value),
        closed=D(closed),
    )


def _position(legs):
    return Position(
        key="tastytrade:5WX01234:10",
        broker="tastytrade",
        account_number="5WX01234",
        opened_at=datetime(2026, 10, 1, tzinfo=UTC),
        legs=legs,
        units=D("1"),
        entry_price=D("2"),
    )


def test_iron_condor_legs_like_tracker2():
    position = _position([
        _leg("SPXW  261120P05780000", "PUT", "5780", +1, "1", "-100"),
        _leg("SPXW  261120P05800000", "PUT", "5800", -1, "1", "200"),
    ])  # fmt: skip
    legs = graph_legs(position, {}, {"SPXW  261120P05800000": D("1.10")})
    assert legs == [
        {
            "leg_type": "PUT", "side": "LONG", "strike": 5780.0,
            "expiration_date": "2026-11-20", "entry_price": -1.0, "quantity": 1,
            "multiplier": 100, "live_price": None, "stream_symbol": ".SPXW261120P5780",
        },
        {
            "leg_type": "PUT", "side": "SHORT", "strike": 5800.0,
            "expiration_date": "2026-11-20", "entry_price": 2.0, "quantity": 1,
            "multiplier": 100, "live_price": 1.1, "stream_symbol": ".SPXW261120P5800",
        },
    ]  # fmt: skip
    assert graph_symbol(position) == "SPX"


def test_partly_closed_leg_graphs_what_is_left():
    position = _position([_leg("SPXW  261120P05800000", "PUT", "5800", -1, "3", "600", "2")])
    [leg] = graph_legs(position, {}, {})
    assert leg["quantity"] == 1 and leg["entry_price"] == 2.0


def test_closed_and_stock_legs_are_left_out():
    stock = PositionLeg("SPY", "SPY", InstrumentType.STOCK, None, +1, D("100"), D("-50000"))
    closed = _leg("SPXW  261120P05800000", "PUT", "5800", -1, "1", "200", "1")
    assert graph_legs(_position([stock, closed]), {}, {}) == []


def test_futures_option_leg_uses_its_own_multiplier_and_no_stream():
    symbol = "./ESZ6 E4AX6 261120P6000"
    position = _position([
        _leg(symbol, "PUT", "6000", -1, "2", "1000",
             kind=InstrumentType.FUTURE_OPTION, underlying="/ESZ6"),
    ])  # fmt: skip
    [leg] = graph_legs(position, {symbol: D("50")}, {})
    assert leg["multiplier"] == 50 and leg["entry_price"] == 10.0 and leg["quantity"] == 2
    assert leg["stream_symbol"] is None
    # The contract, not its root: two months are two prices (graphed on their own tabs)
    assert graph_symbol(position) == "/ESZ6"
