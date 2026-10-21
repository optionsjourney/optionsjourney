from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import Status, build_positions
from optionsjourney.core.stats import TradePoint, summarize, trade_curve
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
)

T0 = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
EXP = date(2026, 10, 16)
_ids = count(1)
S, B = Side.SELL, Side.BUY
KEY = "tastytrade:5WX01234:1"


def opt(side, effect, put_call, strike, *, price="1.00", order="1", at=T0, kind=Kind.TRADE):
    price_ = Decimal(price) if price is not None else None
    value = Decimal("0") if price_ is None else price_ * 100 * (1 if side is Side.SELL else -1)
    return Fill(
        broker="tastytrade",
        account_number="5WX01234",
        transaction_id=str(next(_ids)),
        leg=0,
        executed_at=at,
        kind=kind,
        instrument_type=InstrumentType.OPTION,
        symbol=f"SPX {put_call[0]}{strike}",
        underlying="SPX",
        option=OptionDetails(put_call, Decimal(strike), EXP),
        side=side,
        effect=effect,
        quantity=Decimal("1"),
        price=price_,
        value=value,
        fees=Decimal("1"),
        order_id=order,
    )


def iron_condor_open():
    return [
        opt(B, Effect.OPEN, "PUT", 5780, price="1.00"),
        opt(S, Effect.OPEN, "PUT", 5800, price="2.00"),
        opt(S, Effect.OPEN, "CALL", 6000, price="1.50"),
        opt(B, Effect.OPEN, "CALL", 6020, price="0.50"),
    ]


def test_unsplit_iron_condor_can_be_split():
    [position] = build_positions(group_fills(iron_condor_open())).positions
    assert position.key == KEY
    assert position.can_split
    assert position.entry_price == Decimal("2.00")


def test_split_iron_condor_is_two_positions():
    book = build_positions(group_fills(iron_condor_open()), split={KEY})
    puts, calls = book.positions
    assert (puts.key, calls.key) == (f"{KEY}#put", f"{KEY}#call")
    assert puts.name == "SPX Put Credit Spread" and calls.name == "SPX Call Credit Spread"
    assert puts.entry_price == Decimal("1.00") and calls.entry_price == Decimal("1.00")
    assert puts.split_from == KEY and not puts.can_split


def test_one_order_closing_both_halves_closes_both():
    later = T0 + timedelta(hours=2)
    closing = [
        opt(S, Effect.CLOSE, "PUT", 5780, price="0.10", order="2", at=later),
        opt(B, Effect.CLOSE, "PUT", 5800, price="0.30", order="2", at=later),
        opt(B, Effect.CLOSE, "CALL", 6000, price="0.20", order="2", at=later),
        opt(S, Effect.CLOSE, "CALL", 6020, price="0.05", order="2", at=later),
    ]
    book = build_positions(group_fills(iron_condor_open() + closing), split={KEY})
    assert not book.unmatched
    assert all(p.status is Status.CLOSED for p in book.positions)
    # Puts: +200 -100 credit, -30 +10 to close, 4 fees
    assert book.positions[0].realized_pl == Decimal("76")


def test_one_side_stopped_out_closes_only_that_half():
    later = T0 + timedelta(hours=1)
    stop = [
        opt(S, Effect.CLOSE, "CALL", 6020, price="2.00", order="3", at=later),
        opt(B, Effect.CLOSE, "CALL", 6000, price="5.00", order="3", at=later),
    ]
    puts, calls = build_positions(group_fills(iron_condor_open() + stop), split={KEY}).positions
    assert calls.status is Status.CLOSED
    assert puts.status is Status.OPEN


def test_summary():
    later = T0 + timedelta(hours=1)
    fills = [
        opt(S, Effect.OPEN, "PUT", 5800, price="2.00", order="1"),
        opt(B, Effect.CLOSE, "PUT", 5800, price="1.00", order="2", at=later),  # +98
        opt(S, Effect.OPEN, "PUT", 5700, price="1.00", order="3"),
        opt(B, Effect.CLOSE, "PUT", 5700, price="3.00", order="4", at=later),  # -202
        opt(S, Effect.OPEN, "PUT", 5600, price="1.00", order="5"),  # still open
    ]
    summary = summarize(build_positions(group_fills(fills)).positions)
    assert (summary.trades, summary.open, summary.closed) == (3, 1, 2)
    assert (summary.wins, summary.losses) == (1, 1)
    assert summary.win_rate == Decimal("50")
    assert summary.realized == Decimal("-104")
    assert summary.average == Decimal("-52")
    assert (summary.best, summary.worst) == (Decimal("98"), Decimal("-202"))


def test_empty_summary():
    summary = summarize([])
    assert summary.win_rate is None and summary.average is None and summary.best is None


def times(fill, n):
    """#166: the fill for n contracts."""
    from dataclasses import replace

    return replace(fill, quantity=fill.quantity * n, value=fill.value * n, fees=fill.fees * n)


def uneven_condor_open():
    """A put credit spread x1 and a call credit spread x3 in one order (an ERN-style bot)."""
    ic = iron_condor_open()
    return ic[:2] + [times(f, 3) for f in ic[2:]]


def test_a_condor_with_uneven_sides_can_be_split():
    [whole] = build_positions(group_fills(uneven_condor_open())).positions
    assert whole.can_split
    puts, calls = build_positions(group_fills(uneven_condor_open()), split={KEY}).positions
    assert (puts.units, calls.units) == (1, 3)
    assert puts.entry_price == Decimal("1.00") and calls.entry_price == Decimal("1.00")
    assert calls.name == "SPX Call Credit Spread" and calls.split_from == KEY


def test_uneven_halves_close_together_or_alone():
    later = T0 + timedelta(hours=2)
    closing = [
        opt(S, Effect.CLOSE, "PUT", 5780, price="0.10", order="2", at=later),
        opt(B, Effect.CLOSE, "PUT", 5800, price="0.30", order="2", at=later),
        times(opt(B, Effect.CLOSE, "CALL", 6000, price="0.20", order="2", at=later), 3),
        times(opt(S, Effect.CLOSE, "CALL", 6020, price="0.05", order="2", at=later), 3),
    ]
    book = build_positions(group_fills(uneven_condor_open() + closing), split={KEY})
    assert not book.unmatched and all(p.status is Status.CLOSED for p in book.positions)
    stop = closing[2:]
    puts, calls = build_positions(group_fills(uneven_condor_open() + stop), split={KEY}).positions
    assert calls.status is Status.CLOSED and puts.status is Status.OPEN


def test_four_legs_across_expirations_or_without_two_spreads_cant_split():
    from dataclasses import replace

    ic = iron_condor_open()
    later = OptionDetails("CALL", Decimal("6000"), EXP + timedelta(days=7))
    calendar = ic[:2] + [replace(ic[2], option=later), replace(ic[3], option=replace(
        later, strike=Decimal("6020")))]  # fmt: skip
    [p] = build_positions(group_fills(calendar)).positions
    assert not p.can_split
    two_short_puts = [replace(ic[0], side=S, value=-ic[0].value)] + ic[1:]
    [p] = build_positions(group_fills(two_short_puts)).positions
    assert not p.can_split


def test_trade_curve_adds_up_closed_trades_from_zero():
    """#208 (Tracker2 f52ae8a0, ca044d27): one point per closed trade, oldest close first,
    after a $0 start, so a bot with one trade still draws a line."""
    later = T0 + timedelta(hours=2)
    curve = trade_curve([(later, Decimal("-50")), (T0, Decimal("120"))])
    assert curve == [
        TradePoint(None, None, Decimal("0")),
        TradePoint(T0, Decimal("120"), Decimal("120")),
        TradePoint(later, Decimal("-50"), Decimal("70")),
    ]
    assert len(trade_curve([(T0, Decimal("10"))])) == 2  # the start and the one trade


def test_trade_curve_is_empty_without_closed_trades():
    assert trade_curve([]) == []
