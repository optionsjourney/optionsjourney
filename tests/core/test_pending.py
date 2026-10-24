from datetime import date

from optionsjourney.core import pending
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side

TODAY = date(2026, 10, 13)
SHORT = "SPXW  261013P05800000"
LONG = "SPXW  261013P05790000"
LATER = "SPXW  261016P05800000"


def _leg(symbol, side, quantity=2, opening=True):
    return BrokerLeg(symbol, side, opening, quantity, quantity)


def _found(*legs, **extra):
    fields = {
        "broker_order_id": "1",
        "status": OrderStatus.WORKING,
        "legs": legs or (_leg(SHORT, Side.SELL), _leg(LONG, Side.BUY)),
    }
    return BrokerOrder(**{**fields, **extra})


def test_category_0dte_calendars_options():
    assert pending.category(_found(), TODAY) == "0dte"
    assert pending.category(_found(_leg(SHORT, Side.SELL), _leg(LATER, Side.BUY)), TODAY) == (
        "calendars"
    )
    assert pending.category(_found(_leg(LATER, Side.SELL)), TODAY) == "options"
    assert pending.category(_found(_leg("./ESZ6 EW2Z6 261218P7000", Side.SELL)), TODAY) == (
        "options"
    )
    assert pending.category(_found(_leg(SHORT, Side.SELL), _leg("junk", Side.BUY)), TODAY) == (
        "options"
    )
