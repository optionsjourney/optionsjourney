"""The P/L-against-cost line and the buy/sell marks (#236)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from itertools import count

from optionsjourney.core.stock_history import (
    held_by_day,
    range_start,
    share_moves,
    stock_transactions,
    trade_marks,
    value_series,
)
from optionsjourney.core.transactions import Effect, Fill, InstrumentType, Kind, OptionDetails, Side

DAY = date(2026, 10, 20)
_ids = count(1)


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
    assert trade_marks(fills, "ABC", accounts=("schwab:1",)) == []
    assert len(trade_marks(fills, "ABC", accounts=("tastytrade:1",))) == 2


def test_assignment_shares_are_marked():
    marks = trade_marks([fill(Side.BUY, 100, 50, 5, fill_kind=Kind.ASSIGNMENT)], "ABC")
    assert [(m.side, m.quantity, m.price) for m in marks] == [("buy", D(100), D(50))]


D1, D2, D3 = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)


def _fill(day, account, ticker, side, qty, kind=Kind.TRADE):
    broker, number = account.split(":")
    return Fill(
        broker=broker, account_number=number, transaction_id=str(next(_ids)), leg=0,
        executed_at=datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC), kind=kind,
        instrument_type=InstrumentType.STOCK, symbol=ticker, underlying=ticker, option=None,
        side=side, effect=None, quantity=D(qty), price=D(10), value=D(0), fees=D(0),
    )  # fmt: skip


def test_walks_back_from_today():
    moves = share_moves([_fill(D2, "tt:1", "AAPL", Side.BUY, 10)])
    held = held_by_day({("tt:1", "AAPL"): D(30)}, moves, [D1, D2, D3])
    assert held == {D1: {"AAPL": D(20)}, D2: {"AAPL": D(30)}, D3: {"AAPL": D(30)}}


def test_sold_out_ticker_shows_before_the_sale_only():
    moves = share_moves([_fill(D2, "tt:1", "MSFT", Side.SELL, 5)])
    assert held_by_day({}, moves, [D1, D2]) == {D1: {"MSFT": D(5)}, D2: {}}


def test_assignment_and_exercise_are_moves():
    fills = [
        _fill(D2, "tt:1", "AAPL", Side.BUY, 100, Kind.ASSIGNMENT),
        _fill(D3, "tt:1", "AAPL", Side.SELL, 100, Kind.EXERCISE),
    ]
    assert [m.quantity for m in share_moves(fills)] == [D(100), D(-100)]


def test_accounts_filter_moves():
    fills = [_fill(D2, "tt:1", "AAPL", Side.BUY, 10), _fill(D2, "schwab:2", "AAPL", Side.BUY, 7)]
    assert [m.account for m in share_moves(fills, accounts=("tt:1",))] == ["tt:1"]


def test_long_never_goes_negative():  # a buy without the shares to show for it
    moves = share_moves([_fill(D2, "tt:1", "AAPL", Side.BUY, 50)])
    assert held_by_day({("tt:1", "AAPL"): D(10)}, moves, [D1])[D1] == {}


def test_short_stays_short_or_flat():
    moves = share_moves([_fill(D2, "tt:1", "XYZ", Side.SELL, 50)])
    assert held_by_day({("tt:1", "XYZ"): D(-10)}, moves, [D1])[D1] == {}


def test_held_sums_across_accounts():
    moves = share_moves([_fill(D2, "tt:1", "AAPL", Side.BUY, 10)])
    today = {("tt:1", "AAPL"): D(10), ("schwab:2", "AAPL"): D(5)}
    assert held_by_day(today, moves, [D1, D2]) == {D1: {"AAPL": D(5)}, D2: {"AAPL": D(15)}}


def test_value_uses_close_then_lookback_then_leaves_out():
    held = {D1: {"A": D(2), "B": D(1)}, D2: {"A": D(2), "B": D(1)}}
    closes = {"A": {D1: D(10), D2: D(11)}, "B": {D1: D(5)}}
    pts = value_series(held, closes, [D1, D2])
    assert [(p.value, p.priced, p.total) for p in pts] == [(D(25), 2, 2), (D(27), 2, 2)]
    far = {"A": {D1: D(10)}, "B": {date(2026, 2, 1): D(5)}}
    assert value_series({D1: held[D1]}, far, [D1])[0].priced == 1


def test_leading_empty_days_are_dropped():
    pts = value_series({D1: {}, D2: {"A": D(1)}}, {"A": {D2: D(3)}}, [D1, D2])
    assert [p.day for p in pts] == [D2]


def test_later_empty_days_stay_at_zero():
    pts = value_series({D1: {"A": D(1)}, D2: {}}, {"A": {D1: D(3)}}, [D1, D2])
    assert [(p.day, p.value, p.total) for p in pts] == [(D1, D(3), 1), (D2, D(0), 0)]


def test_transaction_list_is_newest_first_with_actions():
    fills = [
        _fill(D1, "tt:1", "AAPL", Side.BUY, 10),
        _fill(D3, "tt:1", "AAPL", Side.SELL, 100, Kind.EXERCISE),
        _fill(D2, "tt:1", "AAPL", Side.BUY, 100, Kind.ASSIGNMENT),
    ]
    rows = stock_transactions(fills)
    assert [(r.ticker, r.action) for r in rows] == [
        ("AAPL", "Exercised"),
        ("AAPL", "Assigned"),
        ("AAPL", "Buy"),
    ]


def test_fills_without_a_side_or_not_stock_are_ignored():
    no_side = replace(_fill(D1, "tt:1", "AAPL", Side.BUY, 10), side=None)
    option = replace(_fill(D1, "tt:1", "AAPL", Side.BUY, 10), instrument_type=InstrumentType.OPTION)
    assert share_moves([no_side, option]) == []
    assert stock_transactions([no_side, option]) == []


def test_transaction_fields():
    f = replace(_fill(D1, "tt:1", "AAPL", Side.SELL, 4), price=D(12), value=D(48), fees=D("0.5"))
    [row] = stock_transactions([f])
    assert (row.executed_at, row.account, row.ticker, row.action) == (
        f.executed_at, "tt:1", "AAPL", "Sell",
    )  # fmt: skip
    assert (row.quantity, row.price, row.value, row.fees) == (D(4), D(12), D(48), D("0.5"))


def test_accounts_filter_transactions():
    fills = [_fill(D2, "tt:1", "AAPL", Side.BUY, 10), _fill(D2, "schwab:2", "AAPL", Side.BUY, 7)]
    assert [r.account for r in stock_transactions(fills, accounts=("schwab:2",))] == ["schwab:2"]


def test_clamp_logs_once_per_key(caplog):
    moves = share_moves([_fill(D3, "tt:1", "AAPL", Side.BUY, 50)])
    with caplog.at_level("INFO", logger="optionsjourney.core.stock_history"):
        held_by_day({("tt:1", "AAPL"): D(10)}, moves, [D1, D2, D3])
    assert len(caplog.records) == 1


def test_lookback_is_the_latest_close_within_ten_days():
    held = {D3: {"A": D(1)}}
    ok = {"A": {D3 - timedelta(days=10): D(7), D3 - timedelta(days=12): D(1)}}
    assert value_series(held, ok, [D3])[0].value == 7
    stale = {"A": {D3 - timedelta(days=11): D(7)}}
    assert value_series(held, stale, [D3])[0].priced == 0
