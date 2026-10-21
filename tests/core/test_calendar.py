from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

from optionsjourney.core.calendar import (
    DayPL,
    by_month,
    daily_pl,
    equity_curve,
    max_drawdown,
    month_grid,
)
from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import build_positions
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
)

T0 = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)  # Tue Sep 1, 10:00 ET
_ids = count(1)


def opt(side, effect, strike, *, qty=1, price, order, at=T0):
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
        symbol=f"SPX P{strike}",
        underlying="SPX",
        option=OptionDetails("PUT", Decimal(strike), date(2026, 10, 16)),
        side=side,
        effect=effect,
        quantity=qty,
        price=price,
        value=price * qty * 100 * (1 if side is Side.SELL else -1),
        fees=Decimal("0"),
        order_id=order,
    )


S, B = Side.SELL, Side.BUY
OPEN, CLOSE = Effect.OPEN, Effect.CLOSE


def positions(*fills):
    return build_positions(group_fills(fills)).positions


def test_pl_lands_on_the_day_it_was_realized():
    # Opened Sep 1; half closed Sep 2 (+$100), the rest Sep 3 (-$50)
    days = daily_pl(
        positions(
            opt(S, OPEN, 5800, qty=2, price="2.00", order="1"),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=T0 + timedelta(days=1)),
            opt(B, CLOSE, 5800, price="2.50", order="3", at=T0 + timedelta(days=2)),
        )
    )
    assert set(days) == {date(2026, 9, 2), date(2026, 9, 3)}
    assert days[date(2026, 9, 2)].realized == Decimal("100")
    assert days[date(2026, 9, 3)].realized == Decimal("-50")


def test_a_trade_counts_once_on_the_day_it_closes():
    days = daily_pl(
        positions(
            opt(S, OPEN, 5800, qty=2, price="2.00", order="1"),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=T0 + timedelta(days=1)),
            opt(B, CLOSE, 5800, price="2.50", order="3", at=T0 + timedelta(days=2)),
        )
    )
    partial_day, close_day = days[date(2026, 9, 2)], days[date(2026, 9, 3)]
    assert (partial_day.trades, partial_day.partials) == (0, 1)
    assert (close_day.trades, close_day.partials) == (1, 0)
    # Won or lost on the whole trade: +100 - 50 = +50
    assert (close_day.wins, close_day.losses) == (1, 0)


def test_days_follow_new_york_time():
    # 01:30 UTC on Sep 3 is still Sep 2 in New York
    late = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)
    days = daily_pl(
        positions(
            opt(S, OPEN, 5800, price="2.00", order="1"),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=late),
        )
    )
    assert set(days) == {date(2026, 9, 2)}


def test_several_trades_on_one_day_add_up():
    close_at = T0 + timedelta(hours=2)
    day = daily_pl(
        positions(
            opt(S, OPEN, 5800, price="2.00", order="1"),
            opt(S, OPEN, 5700, price="1.00", order="2"),
            opt(B, CLOSE, 5800, price="1.00", order="3", at=close_at),
            opt(B, CLOSE, 5700, price="3.00", order="4", at=close_at),
        )
    )[date(2026, 9, 1)]
    assert day.realized == Decimal("-100")
    assert (day.trades, day.wins, day.losses) == (2, 1, 1)


def test_open_trades_add_nothing():
    assert daily_pl(positions(opt(S, OPEN, 5800, price="2.00", order="1"))) == {}


# --- The month grid -------------------------------------------------------------


def test_month_grid_has_weekday_columns_and_week_totals():
    days = daily_pl(
        positions(
            opt(S, OPEN, 5800, price="2.00", order="1"),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=T0),  # Tue Sep 1: +100
            opt(S, OPEN, 5700, price="1.00", order="3", at=T0 + timedelta(days=2)),
            opt(B, CLOSE, 5700, price="1.50", order="4", at=T0 + timedelta(days=2)),  # Thu: -50
        )
    )
    grid = month_grid(2026, 9, days)
    first = grid.weeks[0]
    # Mon Aug 31 is outside the month; Tue Sep 1 to Fri Sep 4 are in it
    assert [cell.day for cell in first.days] == [date(2026, 8, 31)] + [
        date(2026, 9, d) for d in range(1, 5)
    ]
    assert not first.days[0].in_month
    assert first.days[1].pl.realized == Decimal("100")
    assert first.total == Decimal("50")
    assert grid.total == Decimal("50")
    assert len(grid.weeks) == 5  # weeks starting Aug 31, Sep 7, 14, 21, 28


def test_weekend_pl_counts_in_its_week_and_month():
    saturday = datetime(2026, 9, 5, 14, 0, tzinfo=UTC)
    days = daily_pl(
        positions(
            opt(S, OPEN, 5800, price="2.00", order="1"),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=saturday),
        )
    )
    grid = month_grid(2026, 9, days)
    assert grid.weeks[0].total == Decimal("100")
    assert grid.weeks[0].weekend == Decimal("100")
    assert grid.total == Decimal("100")


def test_shading_scales_to_the_month_best_and_worst_days():
    close = T0 + timedelta(hours=1)
    day2, day3 = close + timedelta(days=1), close + timedelta(days=2)
    days = daily_pl(
        positions(
            opt(S, OPEN, 5800, price="4.00", order="1"),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=close),  # Sep 1: +300
            opt(S, OPEN, 5700, price="2.00", order="3", at=day2),
            opt(B, CLOSE, 5700, price="1.00", order="4", at=day2),  # Sep 2: +100
            opt(S, OPEN, 5600, price="1.00", order="5", at=day3),
            opt(B, CLOSE, 5600, price="3.00", order="6", at=day3),  # Sep 3: -200
        )
    )
    cells = {cell.day: cell for week in month_grid(2026, 9, days).weeks for cell in week.days}
    assert cells[date(2026, 9, 1)].shade == "gain-3"  # the best day
    assert cells[date(2026, 9, 2)].shade == "gain-2"  # a third of the best, or more
    assert cells[date(2026, 9, 3)].shade == "loss-3"  # the worst day
    assert cells[date(2026, 9, 4)].shade == ""  # nothing that day


def test_pl_on_a_weekend_before_the_first_weekday_is_kept():
    # Aug 2026 starts on a Saturday
    saturday = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)
    days = daily_pl(
        positions(
            opt(S, OPEN, 5800, price="2.00", order="1", at=saturday - timedelta(days=3)),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=saturday),
        )
    )
    grid = month_grid(2026, 8, days)
    assert grid.total == Decimal("100")
    assert sum(week.total for week in grid.weeks) == Decimal("100")


def test_closes_of_one_trade_on_its_closing_day_are_not_partials():
    # A spread closed leg by leg within the day: one trade closed, no partials
    later = T0 + timedelta(hours=1)
    day = daily_pl(
        positions(
            opt(S, OPEN, 5800, price="2.00", order="1"),
            opt(B, OPEN, 5700, price="1.00", order="1"),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=later),
            opt(S, CLOSE, 5700, price="0.50", order="3", at=later + timedelta(minutes=5)),
        )
    )[date(2026, 9, 1)]
    assert (day.trades, day.partials) == (1, 0)


def test_several_partial_closes_of_one_trade_in_a_day_count_once():
    later = T0 + timedelta(hours=1)
    day = daily_pl(
        positions(
            opt(S, OPEN, 5800, qty=3, price="2.00", order="1"),
            opt(B, CLOSE, 5800, price="1.00", order="2", at=later),
            opt(B, CLOSE, 5800, price="1.00", order="3", at=later + timedelta(minutes=5)),
        )
    )[date(2026, 9, 1)]
    assert (day.trades, day.partials) == (0, 1)


# --- P/L over time ----------------------------------------------------------------


def _days(*amounts):
    return {
        date(2026, 9, i + 1): DayPL(realized=Decimal(a), trades=1) for i, a in enumerate(amounts)
    }


def test_equity_curve_adds_up_day_by_day():
    curve = equity_curve(_days("100", "-50", "200"))
    assert [p.day for p in curve] == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    assert [p.cumulative for p in curve] == [Decimal("100"), Decimal("50"), Decimal("250")]


def test_drawdown_is_the_fall_from_the_highest_point_so_far():
    curve = equity_curve(_days("100", "-50", "-100", "200"))
    assert [p.drawdown for p in curve] == [Decimal(n) for n in ("0", "-50", "-150", "0")]


def test_drawdown_counts_from_zero_when_the_first_days_lose():
    # The peak starts at zero, as in Tracker2, so early losses are drawdown
    curve = equity_curve(_days("-100", "50"))
    assert [p.drawdown for p in curve] == [Decimal("-100"), Decimal("-50")]


def test_days_without_closes_are_left_out():
    days = _days("100", "0")
    days[date(2026, 9, 2)] = DayPL()  # nothing realized, no trades
    assert [p.day for p in equity_curve(days)] == [date(2026, 9, 1)]


def test_max_drawdown_is_the_deepest_point():
    deepest = max_drawdown(equity_curve(_days("100", "-50", "-100", "200", "-30")))
    assert (deepest.day, deepest.drawdown) == (date(2026, 9, 3), Decimal("-150"))
    assert max_drawdown(equity_curve(_days("100", "200"))) is None


def test_by_month_sums_each_month():
    days = _days("100", "-150")
    days[date(2026, 10, 1)] = DayPL(realized=Decimal("80"), trades=1)
    rows = by_month(equity_curve(days))
    assert [(r.month, r.pl, r.cumulative, r.drawdown) for r in rows] == [
        (date(2026, 9, 1), Decimal("-50"), Decimal("-50"), Decimal("-150")),
        (date(2026, 10, 1), Decimal("80"), Decimal("30"), Decimal("-70")),
    ]
