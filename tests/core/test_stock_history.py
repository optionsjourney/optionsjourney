"""The P/L-against-cost line and the buy/sell marks (#236)."""

from datetime import UTC, date, datetime
from decimal import Decimal as D
from itertools import count

from optionsjourney.core.stock_history import StockRow, portfolio_series, range_start, trade_marks
from optionsjourney.core.transactions import Effect, Fill, InstrumentType, Kind, OptionDetails, Side

DAY = date(2026, 10, 20)
_ids = count(1)


def row(q, cost, close, *, day=DAY, account="tastytrade:1", ticker="ABC"):
    return StockRow(day, account, ticker, D(q), D(cost), None if close is None else D(close))


def test_day_pl_long_and_short():
    [d] = portfolio_series([row(100, 50, 55), row(-10, 200, 190)])
    assert d.pl == 600 and d.cost == 7000
    assert d.pct == D(600) / D(7000) * 100
    assert d.priced == d.total == 2


def test_unpriced_rows_left_out_and_counted():
    [d] = portfolio_series([row(100, 50, 55), row(10, 20, None)])
    assert (d.pl, d.cost, d.priced, d.total) == (500, 5000, 1, 2)


def test_account_and_ticker_filter():
    rows = [row(10, 10, 11), row(10, 10, 12, account="schwab:2"), row(5, 10, 11, ticker="XYZ")]
    assert portfolio_series(rows, ticker="ABC")[0].pl == 30
    assert portfolio_series(rows, account="tastytrade:1", ticker="ABC")[0].pl == 10
    assert portfolio_series(rows, account="tastytrade:1")[0].pl == 15


def test_series_sorted_by_day_with_few_points():
    days = [date(2026, 10, 3), date(2026, 10, 1), date(2026, 10, 2)]
    assert [d.day for d in portfolio_series([row(1, 1, 2, day=x) for x in days])] == sorted(days)


def test_range_start():
    assert range_start("1M", date(2026, 10, 21)) == date(2026, 9, 21)
    assert range_start("All", date(2026, 10, 21)) is None
    assert range_start("bogus", date(2026, 10, 21)) is None


def fill(side, qty, price, day, *, symbol="ABC", kind=InstrumentType.STOCK, fill_kind=Kind.TRADE):
    option = (
        OptionDetails("CALL", D(5), date(2026, 12, 18)) if kind is InstrumentType.OPTION else None
    )
    return Fill(
        broker="tastytrade", account_number="1", transaction_id=str(next(_ids)), leg=0,
        executed_at=datetime(2026, 10, day, 15, 0, tzinfo=UTC), kind=fill_kind,
        instrument_type=kind, symbol=symbol, underlying=symbol, option=option, side=side,
        effect=Effect.OPEN, quantity=D(qty), price=D(price), value=D(0), fees=D(0),
    )  # fmt: skip


def test_trade_marks():
    fills = [
        fill(Side.BUY, 30, 10, 1), fill(Side.BUY, 10, 12, 1), fill(Side.SELL, 40, 13, 2),
        fill(Side.BUY, 1, 1, 1, kind=InstrumentType.OPTION), fill(Side.BUY, 5, 9, 1, symbol="XYZ"),
    ]  # fmt: skip
    marks = trade_marks(fills, "ABC")
    assert [(m.day, m.side, m.quantity, m.price) for m in marks] == [
        (date(2026, 10, 1), "buy", D(40), D("10.5")),
        (date(2026, 10, 2), "sell", D(40), D(13)),
    ]
    assert trade_marks(fills, "ABC", account="schwab:1") == []
    assert len(trade_marks(fills, "ABC", account="tastytrade:1")) == 2


def test_assignment_shares_are_marked():
    marks = trade_marks([fill(Side.BUY, 100, 50, 5, fill_kind=Kind.ASSIGNMENT)], "ABC")
    assert [(m.side, m.quantity, m.price) for m in marks] == [("buy", D(100), D(50))]
