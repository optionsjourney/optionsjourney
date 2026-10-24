"""The Stocks tab's holdings (#58), from the brokers' own positions."""

from decimal import Decimal

from optionsjourney.core.holdings import Holding, StockPosition, holdings, stock_summary


def held(ticker, quantity, basis, account="tastytrade:A", close=None):
    return StockPosition(
        account,
        ticker,
        Decimal(quantity),
        Decimal(basis),
        None if close is None else Decimal(close),
    )


def test_one_row_per_ticker_across_accounts():
    rows = holdings(
        [held("AAPL", "10", "250", "tastytrade:A"), held("AAPL", "30", "260", "schwab:B")],
        prices={"AAPL": Decimal("270")},
        prev_closes={"AAPL": Decimal("265")},
    )
    row = rows[0]
    assert (row.ticker, row.quantity, row.cost_basis) == ("AAPL", Decimal("40"), Decimal("257.5"))
    assert row.cost == Decimal("10300")
    assert row.pl == Decimal("500") and round(row.pl_percent, 4) == Decimal("4.8544")
    assert row.today_pl == Decimal("200")  # 40 x (270 - 265)


def test_previous_close_falls_back_to_the_brokers_own():
    rows = holdings([held("IRM", "51.95", "38.56", close="114.51")], {"IRM": Decimal("115")}, {})
    assert rows[0].today_pl == Decimal("51.95") * Decimal("0.49")


def test_short_shares_and_missing_prices():
    row = holdings([held("TSLA", "-10", "400")], {}, {})[0]
    assert row.quantity == Decimal("-10") and row.cost_basis == Decimal("400")
    assert row.price is None and row.pl is None and row.today_pl is None
    row = holdings([held("TSLA", "-10", "400")], {"TSLA": Decimal("390")}, {})[0]
    assert row.pl == Decimal("100") and row.today_pl is None


def test_zero_quantities_are_left_out():
    assert holdings([held("X", "0", "1")], {}, {}) == []


def test_market_value_is_quantity_times_price():
    held = Holding("AAPL", Decimal(10), Decimal(100), Decimal(110), None)
    assert held.market_value == Decimal(1100)
    assert Holding("AAPL", Decimal(10), Decimal(100), None, None).market_value is None


def test_summary_adds_up_priced_holdings():
    rows = [
        Holding("A", Decimal(10), Decimal(100), Decimal(110), Decimal(20)),  # pl 100, mv 1100
        Holding("B", Decimal(-5), Decimal(50), Decimal(40), Decimal(-10)),  # pl 50,  mv -200
        Holding("C", Decimal(3), Decimal(10), None, None),  # unpriced: left out
    ]
    s = stock_summary(rows)
    assert s.total_pl == Decimal(150)
    assert s.total_pl_percent == Decimal(150) / Decimal(1250) * 100  # cost of priced rows only
    assert s.market_value == Decimal(900)
    assert s.day_pl == Decimal(10)
    assert s.day_pl_percent == Decimal(10) / (Decimal(900) - Decimal(10)) * 100


def test_summary_of_nothing():
    s = stock_summary([])
    assert (s.total_pl, s.total_pl_percent, s.market_value, s.day_pl, s.day_pl_percent) == (
        0,
        None,
        0,
        0,
        None,
    )
