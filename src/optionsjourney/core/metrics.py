"""Trading metrics: how the trades, days, weeks and months went.

Follows Tracker2's trading metrics (docs/decisions/0021-product-scope.md),
with one rule for win rate everywhere: winners over all closed trades, so a
break-even trade counts as a trade that didn't win. Tracker2's calendar and
metrics pages disagreed on this."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from optionsjourney.core.calendar import DayPL

ZERO = Decimal("0")
PERIODS = ("day", "week", "month")
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri")


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, ZERO) / len(values) if values else None


def _share(part: int, whole: int) -> Decimal | None:
    return Decimal(part * 100) / whole if whole else None


@dataclass(frozen=True)
class TradeStats:
    trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: Decimal | None  # 0-100
    expectancy: Decimal | None  # average P/L per trade
    average_win: Decimal | None
    average_loss: Decimal | None  # negative
    win_loss_ratio: Decimal | None  # average win / size of average loss
    profit_factor: Decimal | None  # gross wins / size of gross losses
    largest_win: Decimal | None
    largest_loss: Decimal | None
    fees: Decimal  # already taken out of the P/L
    average_fee: Decimal | None


def trade_stats(results: Iterable[Decimal], fees: Iterable[Decimal]) -> TradeStats:
    """From each closed trade's realized P/L and its fees."""
    results, fees = list(results), list(fees)
    wins = [r for r in results if r > 0]
    losses = [r for r in results if r < 0]
    average_win, average_loss = _mean(wins), _mean(losses)
    total_fees = sum(fees, ZERO)
    return TradeStats(
        trades=len(results),
        wins=len(wins),
        losses=len(losses),
        breakeven=len(results) - len(wins) - len(losses),
        win_rate=_share(len(wins), len(results)),
        expectancy=_mean(results),
        average_win=average_win,
        average_loss=average_loss,
        win_loss_ratio=average_win / -average_loss if average_win and average_loss else None,
        profit_factor=sum(wins, ZERO) / -sum(losses, ZERO) if losses else None,
        largest_win=max(wins, default=None),
        largest_loss=min(losses, default=None),
        fees=total_fees,
        average_fee=total_fees / len(results) if results else None,
    )


@dataclass(frozen=True)
class PeriodStats:
    period: str  # "day", "week" or "month"
    count: int  # periods with any P/L
    winning: int
    losing: int
    win_rate: Decimal | None  # winning periods over all of them, 0-100
    average: Decimal | None
    best: Decimal | None
    worst: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None


def period_start(day: date, period: str) -> date:
    """The day, the Monday of its week, or the first of its month."""
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return day


def pl_by_period(days: dict[date, DayPL], period: str) -> dict[date, Decimal]:
    """Realized P/L per day, week or month, keyed by the period's first day."""
    totals: dict[date, Decimal] = {}
    for day, pl in sorted(days.items()):
        if pl.active:
            start = period_start(day, period)
            totals[start] = totals.get(start, ZERO) + pl.realized
    return totals


def period_stats(days: dict[date, DayPL], period: str) -> PeriodStats:
    values = list(pl_by_period(days, period).values())
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    return PeriodStats(
        period=period,
        count=len(values),
        winning=len(wins),
        losing=len(losses),
        win_rate=_share(len(wins), len(values)),
        average=_mean(values),
        best=max(values, default=None),
        worst=min(values, default=None),
        average_win=_mean(wins),
        average_loss=_mean(losses),
    )


@dataclass(frozen=True)
class WeekdayStats:
    name: str
    pl: Decimal
    days: int  # days with any P/L
    winning_days: int
    losing_days: int
    trades: int  # trades that fully closed on this weekday
    wins: int
    losses: int

    @property
    def average(self) -> Decimal | None:
        return self.pl / self.days if self.days else None


def weekday_stats(days: dict[date, DayPL]) -> list[WeekdayStats]:
    """Monday to Friday. Weekend P/L (a broker booking late) is left out."""
    rows = []
    for weekday, name in enumerate(WEEKDAY_NAMES):
        mine = [pl for day, pl in days.items() if day.weekday() == weekday and pl.active]
        rows.append(
            WeekdayStats(
                name=name,
                pl=sum((pl.realized for pl in mine), ZERO),
                days=len(mine),
                winning_days=sum(1 for pl in mine if pl.realized > 0),
                losing_days=sum(1 for pl in mine if pl.realized < 0),
                trades=sum(pl.trades for pl in mine),
                wins=sum(pl.wins for pl in mine),
                losses=sum(pl.losses for pl in mine),
            )
        )
    return rows
