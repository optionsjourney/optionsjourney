from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from optionsjourney.core import ideas

SPX_PUT = "SPXW  261023P06600000"
SPX_PUT_LOW = "SPXW  261023P06550000"


def card(**over):
    base = {
        "label": "Put spread",
        "qty": 2,
        "entry_unit": 1.5,
        "locked": False,
        "multiplier": 100,
        "legs": [
            {"symbol": SPX_PUT, "side": "sell", "ratio": 1},
            {"symbol": SPX_PUT_LOW, "side": "buy", "ratio": 1},
        ],
    }
    base.update(over)
    return base


def too_many_legs():
    return card(legs=[{"symbol": SPX_PUT, "side": "sell", "ratio": 1}] * 9)


@pytest.mark.parametrize(
    ("label", "cards"),
    [
        ("x", []),
        ("x", [card(), card()]),
        ("x", [too_many_legs()]),
        ("x", [card(legs=[{"symbol": "VIX   261021C00020000", "side": "buy", "ratio": 1}])]),
        ("x", [card(legs=[{"symbol": "./ESZ6 E2AZ6 261023C6000", "side": "buy", "ratio": 1}])]),
        ("x", [card(qty=0)]),
        ("x", [card(entry_unit=float("nan"))]),
        ("   ", [card()]),
        ("x", [card(qty=2.7)]),
        ("x", [card(multiplier=0)]),
        ("x", [card(multiplier=1.5)]),
        ("x", [card(legs=[{"symbol": "SPY   261023P00600000", "side": "buy", "ratio": 1}])]),
        ("x", ["not a card"]),
        ("x", [card(legs=["not a leg"])]),
        ("x", [card(legs="not legs")]),
    ],
)
def test_validate_refuses(label, cards):
    with pytest.raises(ideas.IdeaRefused):
        ideas.validate("SPX", label, cards)


def test_validate_cleans_and_cuts_label():
    cleaned = ideas.validate("SPX", "x", [card(label="  " + "a" * 100)])
    assert cleaned[0]["label"] == "a" * 80
    assert cleaned[0]["qty"] == 2


def test_card_pnl_credit_and_debit():
    credit = card(entry_unit=1.5, qty=2)
    assert ideas.card_pnl(credit, Decimal("1.00")) == Decimal("100")
    debit = card(entry_unit=-2.0, qty=2)
    assert ideas.card_pnl(debit, Decimal("-2.50")) == Decimal("100")


def test_card_unit_signs_and_missing_mark():
    marks = {SPX_PUT: Decimal("3.00"), SPX_PUT_LOW: Decimal("1.00")}
    assert ideas.card_unit(card(), marks) == Decimal("2.00")
    assert ideas.card_unit(card(), {SPX_PUT: Decimal("3.00")}) is None


def test_status_live_partly_expired():
    near = card(
        legs=[
            {"symbol": "SPXW  261016P06600000", "side": "sell", "ratio": 1},
            {"symbol": SPX_PUT, "side": "buy", "ratio": 1},
        ]
    )
    during = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)  # 2 PM New York
    after = datetime(2026, 10, 23, 21, 0, tzinfo=UTC)  # 5 PM New York
    assert ideas.status([card()], during) == "live"
    assert ideas.status([card()], after) == "expired"
    assert ideas.status([near], during) == "partly_expired"


def test_settled_unit_intrinsic():
    closes = {("SPX", date(2026, 10, 23)): Decimal("6580")}
    # short 6600 put is 20 in the money, long 6550 put is out
    assert ideas.settled_unit(card(), closes) == Decimal("20")
    assert ideas.settled_unit(card(), {}) is None


def test_pnl_percent():
    assert ideas.pnl_percent(Decimal("100"), card()) == pytest.approx(100 / 300 * 100)
    assert ideas.pnl_percent(Decimal("100"), card(entry_unit=0)) is None
