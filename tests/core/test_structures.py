from datetime import date
from decimal import Decimal

import pytest

from optionsjourney.core.structures import Leg, describe
from optionsjourney.core.transactions import InstrumentType, OptionDetails

OCT9 = date(2026, 10, 9)
OCT16 = date(2026, 10, 16)


def opt(quantity, put_call, strike, expiration=OCT9):
    return Leg(
        symbol=f"{put_call}{strike}{expiration}",
        instrument_type=InstrumentType.OPTION,
        option=OptionDetails(put_call, Decimal(strike), expiration),
        quantity=Decimal(quantity),
    )


def stock(quantity):
    return Leg("AAPL", InstrumentType.STOCK, None, Decimal(quantity))


@pytest.mark.parametrize(
    ("legs", "name"),
    [
        ([opt(-1, "PUT", 5800)], "Short Put"),
        ([opt(2, "CALL", 6000)], "Long Call"),
        # Verticals: credit when the short leg is nearer the money
        ([opt(-1, "PUT", 5800), opt(1, "PUT", 5790)], "Put Credit Spread"),
        ([opt(1, "PUT", 5800), opt(-1, "PUT", 5790)], "Put Debit Spread"),
        ([opt(-1, "CALL", 6000), opt(1, "CALL", 6010)], "Call Credit Spread"),
        ([opt(1, "CALL", 6000), opt(-1, "CALL", 6010)], "Call Debit Spread"),
        ([opt(-2, "PUT", 5800), opt(1, "PUT", 5790)], "Put Ratio Spread"),
        # Time spreads
        ([opt(-1, "PUT", 5800, OCT9), opt(1, "PUT", 5800, OCT16)], "Put Calendar"),
        ([opt(-1, "CALL", 6000, OCT9), opt(1, "CALL", 6010, OCT16)], "Call Diagonal"),
        # Straddles and strangles
        ([opt(-1, "PUT", 5900), opt(-1, "CALL", 5900)], "Short Straddle"),
        ([opt(1, "PUT", 5800), opt(1, "CALL", 6000)], "Long Strangle"),
        # Butterflies
        ([opt(1, "PUT", 5780), opt(-2, "PUT", 5800), opt(1, "PUT", 5820)], "Put Butterfly"),
        (
            [opt(1, "CALL", 6000), opt(-2, "CALL", 6010), opt(1, "CALL", 6030)],
            "Call Broken Wing Butterfly",
        ),
        # Iron condors and butterflies
        (
            [
                opt(1, "PUT", 5780),
                opt(-1, "PUT", 5800),
                opt(-1, "CALL", 6000),
                opt(1, "CALL", 6020),
            ],
            "Iron Condor",
        ),
        (
            [
                opt(1, "PUT", 5880),
                opt(-1, "PUT", 5900),
                opt(-1, "CALL", 5900),
                opt(1, "CALL", 5920),
            ],
            "Iron Butterfly",
        ),
        (
            [
                opt(-1, "PUT", 5780),
                opt(1, "PUT", 5800),
                opt(1, "CALL", 6000),
                opt(-1, "CALL", 6020),
            ],
            "Reverse Iron Condor",
        ),
        (
            [
                opt(-1, "PUT", 5800, OCT9),
                opt(1, "PUT", 5800, OCT16),
                opt(-1, "CALL", 6000, OCT9),
                opt(1, "CALL", 6000, OCT16),
            ],
            "Double Calendar",
        ),
        # Stock
        ([stock(100)], "Long Stock"),
        ([stock(-50)], "Short Stock"),
        ([stock(100), opt(-1, "CALL", 240)], "Covered Call"),
        ([stock(100), opt(1, "PUT", 220)], "Protective Put"),
    ],
)
def test_describe(legs, name):
    assert describe(legs) == name


@pytest.mark.parametrize(
    "legs",
    [
        [opt(-1, "PUT", 5800), opt(-1, "PUT", 5790)],  # two short puts
        [opt(1, "PUT", 5780), opt(-1, "PUT", 5800), opt(-1, "CALL", 6000), opt(2, "CALL", 6020)],
        [opt(1, "PUT", 1), opt(1, "PUT", 2), opt(1, "PUT", 3), opt(1, "PUT", 4), opt(1, "PUT", 5)],
    ],
)
def test_unknown_shapes_are_custom(legs):
    assert describe(legs) == f"Custom ({len(legs)} legs)"


def test_zero_quantity_legs_are_ignored():
    assert describe([opt(-1, "PUT", 5800), opt(0, "PUT", 5790)]) == "Short Put"
