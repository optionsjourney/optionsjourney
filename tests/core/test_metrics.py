from datetime import date
from decimal import Decimal

from optionsjourney.core.calendar import DayPL
from optionsjourney.core.metrics import period_stats, trade_stats, weekday_stats


def D(text):
    return Decimal(text)


# --- Trades -------------------------------------------------------------------------


def test_trade_stats():
    stats = trade_stats(
        [D("300"), D("100"), D("-200"), D("0")],
        fees=[D("2"), D("2"), D("2"), D("2")],
    )
    assert (stats.trades, stats.wins, stats.losses, stats.breakeven) == (4, 2, 1, 1)
    assert stats.win_rate == D("50")  # break-even trades count as trades
    assert stats.expectancy == D("50")  # 200 / 4
    assert stats.average_win == D("200")
    assert stats.average_loss == D("-200")
    assert stats.win_loss_ratio == D("1")
    assert stats.profit_factor == D("2")  # 400 / 200
    assert (stats.largest_win, stats.largest_loss) == (D("300"), D("-200"))
    assert (stats.fees, stats.average_fee) == (D("8"), D("2"))


def test_no_losers_means_no_profit_factor():
    stats = trade_stats([D("100")], fees=[D("1")])
    assert stats.profit_factor is None
    assert stats.win_loss_ratio is None
    assert stats.largest_loss is None


def test_no_trades():
    stats = trade_stats([], fees=[])
    assert stats.trades == 0
    assert stats.win_rate is None and stats.expectancy is None


# --- Periods ------------------------------------------------------------------------


def _days(pairs):
    return {date.fromisoformat(day): DayPL(realized=D(pl), trades=1) for day, pl in pairs}


def test_period_stats_by_day():
    stats = period_stats(
        _days([("2026-09-01", "100"), ("2026-09-02", "-50"), ("2026-09-03", "250")]), "day"
    )
    assert stats.count == 3
    assert (stats.winning, stats.losing) == (2, 1)
    assert stats.average == D("100")
    assert (stats.best, stats.worst) == (D("250"), D("-50"))
    assert stats.average_win == D("175")
    assert stats.average_loss == D("-50")
    assert stats.win_rate.quantize(D("0.1")) == D("66.7")


def test_weeks_start_on_monday():
    # Fri Sep 4 and Mon Sep 7 are in different weeks; Mon Sep 7 and Wed Sep 9 the same
    days = _days([("2026-09-04", "100"), ("2026-09-07", "-30"), ("2026-09-09", "-20")])
    stats = period_stats(days, "week")
    assert stats.count == 2
    assert (stats.best, stats.worst) == (D("100"), D("-50"))


def test_months():
    days = _days([("2026-08-31", "100"), ("2026-09-01", "-30"), ("2026-09-30", "-20")])
    stats = period_stats(days, "month")
    assert stats.count == 2
    assert stats.worst == D("-50")


# --- Days of the week ---------------------------------------------------------------


def test_weekday_stats():
    days = {
        date(2026, 9, 7): DayPL(realized=D("100"), trades=2, wins=2),  # Monday
        date(2026, 9, 14): DayPL(realized=D("-40"), trades=1, losses=1),  # Monday
        date(2026, 9, 8): DayPL(realized=D("30"), trades=1, wins=1),  # Tuesday
        date(2026, 9, 12): DayPL(realized=D("5"), partials=1),  # Saturday: left out
    }
    rows = weekday_stats(days)
    assert [row.name for row in rows] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    monday = rows[0]
    assert (monday.pl, monday.days, monday.winning_days, monday.losing_days) == (D("60"), 2, 1, 1)
    assert monday.average == D("30")
    assert (monday.trades, monday.wins, monday.losses) == (3, 2, 1)
    assert rows[2].days == 0 and rows[2].average is None
