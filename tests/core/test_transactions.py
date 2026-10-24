from datetime import UTC, date, datetime
from decimal import Decimal

from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
    matches_search,
)


def _fill(**overrides):
    values = dict(
        broker="tastytrade",
        account_number="5WX01234",
        transaction_id="1",
        leg=0,
        executed_at=datetime(2026, 10, 9, 13, 32, tzinfo=UTC),
        kind=Kind.TRADE,
        instrument_type=InstrumentType.OPTION,
        symbol="SPXW  261009P05800000",
        underlying="SPX",
        option=OptionDetails("PUT", Decimal("5800.000"), date(2026, 10, 9)),
        side=Side.SELL,
        effect=Effect.OPEN,
        quantity=Decimal("1"),
        price=Decimal("2.5"),
        value=Decimal("250"),
        fees=Decimal("1.72"),
    )
    values.update(overrides)
    return Fill(**values)


def test_option_label():
    assert _fill().instrument_label == "SPX 10/09/26 5800 Put"
    assert _fill(
        option=OptionDetails("CALL", Decimal("586.50"), date(2026, 10, 16))
    ).instrument_label == ("SPX 10/16/26 586.5 Call")


def test_stock_label():
    assert _fill(option=None, underlying="TSLA", symbol="TSLA").instrument_label == "TSLA"


def test_action_labels():
    assert _fill().action_label == "Sell to Open"
    assert _fill(side=Side.BUY, effect=Effect.CLOSE).action_label == "Buy to Close"
    assert _fill(effect=None).action_label == "Sell"
    assert _fill(kind=Kind.EXPIRATION, side=Side.BUY, effect=Effect.CLOSE).action_label == (
        "Expiration"
    )
    assert _fill(side=None, effect=None).action_label == "Trade"


def test_matches_search_needs_every_word_in_any_case():
    assert matches_search("SPX 10/09/26 5800 Put Sell to Open", "spx PUT")
    assert not matches_search("SPX 10/09/26 5800 Put", "spx call")
    assert matches_search("anything", "   ")
