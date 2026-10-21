"""The summary above the journal's trade list, following Tracker2's (#49)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import build_positions
from optionsjourney.core.stats import journal_summary
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
)

T0 = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
_ids = count(1)
S, B = Side.SELL, Side.BUY
OPEN, CLOSE = Effect.OPEN, Effect.CLOSE


def opt(side, effect, strike, *, qty=1, price, order, at=T0, expiration=date(2026, 10, 16)):
    """One SPX put fill with no fees. Sold is +, bought is -."""
    qty, price = Decimal(qty), Decimal(price)
    return Fill(
        broker="tastytrade",
        account_number="5WX01234",
        transaction_id=str(next(_ids)),
        leg=0,
        executed_at=at,
        kind=Kind.TRADE,
        instrument_type=InstrumentType.OPTION,
        symbol=f"SPX {expiration} P{strike}",
        underlying="SPX",
        option=OptionDetails("PUT", Decimal(strike), expiration),
        side=side,
        effect=effect,
        quantity=qty,
        price=price,
        value=price * qty * 100 * (1 if side is Side.SELL else -1),
        fees=Decimal("0"),
        order_id=order,
    )


def shares(side, effect, qty, price, order, at=T0):
    qty, price = Decimal(qty), Decimal(price)
    return Fill(
        broker="tastytrade",
        account_number="5WX01234",
        transaction_id=str(next(_ids)),
        leg=0,
        executed_at=at,
        kind=Kind.TRADE,
        instrument_type=InstrumentType.STOCK,
        symbol="AAPL",
        underlying="AAPL",
        option=None,
        side=side,
        effect=effect,
        quantity=qty,
        price=price,
        value=price * qty * (1 if side is Side.SELL else -1),
        fees=Decimal("0"),
        order_id=order,
    )


def summary(*fills):
    return journal_summary(build_positions(group_fills(fills)).positions)


LATER = T0 + timedelta(hours=1)


def test_closed_trades_premium_and_capture_rate():
    s = summary(
        # 2-lot put spread for 1.00 credit ($200), closed for 0.40 ($80): +$120
        opt(S, OPEN, 5800, qty=2, price="3.00", order="1"),
        opt(B, OPEN, 5790, qty=2, price="2.00", order="1"),
        opt(B, CLOSE, 5800, qty=2, price="1.40", order="2", at=LATER),
        opt(S, CLOSE, 5790, qty=2, price="1.00", order="2", at=LATER),
        # 1-lot short put for $150, closed for $250: -$100
        opt(S, OPEN, 5700, price="1.50", order="3"),
        opt(B, CLOSE, 5700, price="2.50", order="4", at=LATER),
    )
    assert s.closed_pl == Decimal("20")
    assert s.closed_premium == Decimal("350")
    assert s.open_premium == Decimal("0")
    assert s.capture_rate.quantize(Decimal("0.01")) == Decimal("5.71")  # 20 / 350
    assert (s.trades, s.winners) == (2, 1)
    assert s.win_rate == Decimal("50")


def test_per_lot_stats_divide_by_lots():
    s = summary(
        opt(S, OPEN, 5800, qty=2, price="3.00", order="1"),
        opt(B, OPEN, 5790, qty=2, price="2.00", order="1"),
        opt(B, CLOSE, 5800, qty=2, price="1.40", order="2", at=LATER),
        opt(S, CLOSE, 5790, qty=2, price="1.00", order="2", at=LATER),  # +120 on 2 lots
        opt(S, OPEN, 5700, price="1.50", order="3"),
        opt(B, CLOSE, 5700, price="2.50", order="4", at=LATER),  # -100 on 1 lot
    )
    assert s.avg_per_lot == Decimal("20") / 3  # all P/L over all lots
    assert s.avg_winner_per_lot == Decimal("60")
    assert s.avg_loser_per_lot == Decimal("-100")
    assert (s.max_winner_per_lot, s.max_loser_per_lot) == (Decimal("60"), Decimal("-100"))


def test_open_trades_count_as_open_premium():
    s = summary(opt(S, OPEN, 5800, qty=2, price="1.50", order="1"))
    assert s.open_premium == Decimal("300")
    assert s.closed_premium == Decimal("0")
    assert s.capture_rate is None
    assert (s.trades, s.winners) == (1, 0)


def test_a_partly_closed_trade_splits_its_premium():
    # 2 short puts for $150 each; one bought back for $50: +100 realized
    s = summary(
        opt(S, OPEN, 5800, qty=2, price="1.50", order="1"),
        opt(B, CLOSE, 5800, price="0.50", order="2", at=LATER),
    )
    assert (s.open_premium, s.closed_premium) == (Decimal("150"), Decimal("150"))
    assert s.closed_pl == Decimal("100")
    assert s.avg_per_lot == Decimal("100")  # one lot closed


def test_shares_are_not_premium():
    s = summary(
        shares(B, OPEN, "10", "200", "1"),
        shares(S, CLOSE, "10", "210", "2", at=LATER),
    )
    assert s.closed_pl == Decimal("100")
    assert s.closed_premium == Decimal("0")
    assert s.capture_rate is None


def test_an_open_rolled_trade_keeps_its_premium_open_and_its_pl_out():
    # Like Tracker2: what a roll realized counts once the whole trade closes
    nov = date(2026, 11, 20)
    s = summary(
        opt(S, OPEN, 5800, price="1.50", order="1"),
        opt(B, CLOSE, 5800, price="2.50", order="2", at=LATER),
        opt(S, OPEN, 5800, price="3.00", order="2", at=LATER, expiration=nov),
    )
    assert s.closed_pl == Decimal("0")
    assert s.open_premium == Decimal("200")  # 150 - 250 + 300, the trade's net credit
    assert s.closed_premium == Decimal("0")


def test_on_a_day_an_open_trade_counts_only_that_days_closes():
    # The "Open + closed today" view: a partial close from before isn't today's P/L
    fills = (
        opt(S, OPEN, 5800, qty=2, price="1.50", order="1"),
        opt(B, CLOSE, 5800, price="0.50", order="2", at=LATER),
    )
    positions = build_positions(group_fills(fills)).positions
    assert journal_summary(positions, day=date(2026, 9, 2)).closed_pl == Decimal("0")
    assert journal_summary(positions, day=date(2026, 9, 1)).closed_pl == Decimal("100")
