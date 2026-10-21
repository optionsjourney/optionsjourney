from datetime import date
from decimal import Decimal

import pytest

from optionsjourney.core.symbols import (
    normalize_underlying,
    occ_symbol,
    parse_futures_option,
    parse_occ,
)
from optionsjourney.core.transactions import OptionDetails


@pytest.mark.parametrize(
    ("symbol", "underlying", "details"),
    [
        ("SPXW  261009P05800000", "SPX", OptionDetails("PUT", Decimal("5800"), date(2026, 10, 9))),
        (
            "SPY   261016C00586500",
            "SPY",
            OptionDetails("CALL", Decimal("586.5"), date(2026, 10, 16)),
        ),
        ("TSLA261120P00240000", "TSLA", OptionDetails("PUT", Decimal("240"), date(2026, 11, 20))),
    ],
)
def test_parse_occ(symbol, underlying, details):
    assert parse_occ(symbol) == (underlying, details)


@pytest.mark.parametrize("symbol", ["SPY", "", "./ESZ6 EW2V6 261009P5800", "SPXW 26100P05800000"])
def test_parse_occ_rejects_non_occ(symbol):
    assert parse_occ(symbol) is None


def test_futures_option_from_description():
    details = parse_futures_option("Sold 1 /ESM6 EWJ6 04/30/26 Put 6075.00 @ 44.25", None)
    assert details == OptionDetails("PUT", Decimal("6075.00"), date(2026, 4, 30))


def test_futures_option_from_symbol():
    details = parse_futures_option(None, "./ESM6 EWJ6 260430C6100")
    assert details == OptionDetails("CALL", Decimal("6100"), date(2026, 4, 30))


def test_futures_option_unreadable():
    assert parse_futures_option("Bought 1 /ES", "/ESZ6") is None


def test_normalize_underlying():
    assert normalize_underlying("spxw") == "SPX"
    assert normalize_underlying("QQQ") == "QQQ"
    assert normalize_underlying("$SPX") == "SPX"


def test_occ_symbol_round_trips():
    details = OptionDetails("PUT", Decimal("6600"), date(2025, 10, 24))
    assert occ_symbol("SPXW", details) == "SPXW  251024P06600000"
    assert parse_occ(occ_symbol("SPXW", details)) == ("SPX", details)
    half = OptionDetails("CALL", Decimal("417.5"), date(2026, 1, 16))
    assert occ_symbol("QQQ", half) == "QQQ   260116C00417500"
