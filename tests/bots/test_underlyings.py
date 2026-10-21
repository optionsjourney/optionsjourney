from decimal import Decimal

import pytest

from optionsjourney.bots import market_filters, underlyings

D = Decimal


def test_the_five_underlyings_in_form_order():
    assert underlyings.SYMBOLS == ("SPX", "XSP", "SPY", "QQQ", "IWM")


@pytest.mark.parametrize(
    ("symbol", "root", "american", "watch", "divisor", "grid"),
    [
        ("SPX", "SPXW", False, "SPX", 1, "0.05"),
        ("XSP", "XSP", False, "SPX", 10, "0.01"),
        ("SPY", "SPY", True, "SPY", 1, "0.01"),
        ("QQQ", "QQQ", True, "QQQ", 1, "0.01"),
        ("IWM", "IWM", True, "IWM", 1, "0.01"),
    ],
)
def test_each_row(symbol, root, american, watch, divisor, grid):
    assert underlyings.root(symbol) == root
    assert underlyings.american(symbol) is american
    assert underlyings.watch_symbol(symbol) == watch
    assert underlyings.divisor(symbol) == D(divisor)
    assert underlyings.grid(symbol) == D(grid)


def test_lower_case_is_read_as_upper():
    assert underlyings.get("xsp").symbol == "XSP"
    assert underlyings.root("spx") == "SPXW"


def test_any_other_ticker_is_a_stock():
    """The author's ruling (Tracker2's free-text f-underlying): a stock or ETF trades its
    own root and price, settles in shares, on a cent grid."""
    aapl = underlyings.get("AAPL")
    assert (aapl.root, aapl.american, aapl.watch_symbol, aapl.divisor, aapl.grid) == (
        "AAPL", True, "AAPL", D(1), D("0.01"))  # fmt: skip


@pytest.mark.parametrize(
    ("symbol", "words"),
    [
        ("RUT", "Bots trade SPX and XSP among the indexes, not RUT."),
        ("VIX", "not VIX"),
        ("/ESZ6", "Enter the root, /ES, not a contract month."),
        ("/", "Enter a stock or ETF ticker"),
        ("/es", "Enter a stock or ETF ticker"),
        ("/ABCDE", "Enter a stock or ETF ticker"),
        ("", "Enter a stock or ETF ticker"),
        ("aapl", "Enter a stock or ETF ticker"),
        ("TOOLONGTICKER", "Enter a stock or ETF ticker"),
        ("SPXW", "Enter SPX: the bot trades its weekly (SPXW) options itself."),
        ("XSPW", "Enter XSP"),
        ("NDXP", "not NDXP"),
        ("MRUT", "not MRUT"),
        ("XND", "not XND"),
        ("BRK.B", "BRK.B can't be traded by a bot yet"),
    ],
)
def test_what_isnt_an_underlying(symbol, words):
    assert words in underlyings.problem(symbol)


def test_an_index_or_futures_is_neither_cash_nor_shares_here():
    assert not underlyings.american("RUT") and not underlyings.american("/ES")
    assert underlyings.get("RUT") is None and underlyings.root("RUT") == "RUT"
    assert underlyings.get("SPXW") is None and underlyings.get("BRK.B") is None


def test_a_watch_price_is_in_the_traded_underlyings_points():
    assert underlyings.in_points("XSP", D("5803.20")) == D("580.32")
    assert underlyings.in_points("SPY", D("580.32")) == D("580.32")


def test_the_market_filters_read_the_same_symbol():
    assert market_filters.dx_symbol("XSP") == "SPX"
    assert market_filters.dx_symbol("spy") == "SPY"


@pytest.mark.parametrize("root", ["/ES", "/MES", "/GC", "/M2K", "/NQ"])
def test_a_futures_root_is_an_underlying(root):
    """Bots slice 17 (Tracker2's analyzer_bp._TICKER_RE): a futures root, never a contract
    month. It trades through its series (bots/futures.py), not an option root."""
    found = underlyings.get(root)
    assert found is not None and found.settles == "futures"
    assert underlyings.futures(root) and not underlyings.american(root)
    assert underlyings.problem(root) is None
    assert (underlyings.root(root), underlyings.watch_symbol(root)) == (root, root)
    assert underlyings.grid(root) == D("0.01")


def test_only_futures_roots_are_futures():
    assert not underlyings.futures("SPX") and not underlyings.futures("AAPL")
    assert not underlyings.futures("/ESZ6") and not underlyings.futures(None)
    assert "/ES" in underlyings.SUGGESTED and "/MES" in underlyings.SUGGESTED
