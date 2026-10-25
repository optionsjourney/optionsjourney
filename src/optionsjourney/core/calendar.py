"""The P/L calendar: realized P/L per day, laid out as a month.

Follows Tracker2's calendar (docs/decisions/0021-product-scope.md), on New
York dates rather than UTC:

- P/L lands on the day it was realized: every close, expiration, assignment or
  roll books its own P/L on its own day, so a trade closed in parts spreads
  over several days.
- A trade counts once, on the day it fully closes, and is won or lost on its
  whole P/L. Earlier closes of it count as partials on their days.
- The month is shown Monday to Friday with a total per week. The rare P/L
  booked on a weekend (a broker posting late) counts in its week and month.
- Days are shaded by how they compare with the month's best and worst day."""

from calendar import Calendar, monthrange
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from optionsjourney.core.market import market_date
from optionsjourney.core.positions import EventKind, Position, Status

ZERO = Decimal("0")
WEEKDAYS = 5  # Monday to Friday
# A day at least this share of the month's best (or worst) day gets this shade
SHADES = ((Decimal("0.67"), 3), (Decimal("0.33"), 2), (ZERO, 1))


@dataclass
class DayPL:
    realized: Decimal = ZERO
    trades: int = 0  # trades that fully closed this day
    partials: int = 0  # trades closed in part this day that stayed open
    wins: int = 0
    losses: int = 0

    @property
    def active(self) -> bool:
        return bool(self.trades or self.partials)


def daily_pl(positions: Iterable[Position]) -> dict[date, DayPL]:
    """Realized P/L and trade counts per New York date."""
    days: dict[date, DayPL] = {}
    for position in positions:
        closes = [event for event in position.events if event.kind is not EventKind.OPEN]
        if not closes:
            continue
        closed_on = market_date(closes[-1].at) if position.status is Status.CLOSED else None
        for event in closes:
            days.setdefault(market_date(event.at), DayPL()).realized += event.realized
        for when in {market_date(event.at) for event in closes}:
            day = days[when]
            if when == closed_on:
                day.trades += 1
                day.wins += position.realized_pl > 0
                day.losses += position.realized_pl < 0
            else:
                day.partials += 1  # closed in part this day, and stayed open
    return days


@dataclass(frozen=True)
class TradePL:
    position: Position
    realized: Decimal  # booked from start to end
    closed: bool  # fully closed in the period (daily_pl's trade count)


def realized_between(positions: Iterable[Position], start: date, end: date) -> list[TradePL]:
    """Each trade's P/L booked from start to end (New York dates, both included), on
    the same days daily_pl books it, so the trades add up to the calendar's cells."""
    found = []
    for position in positions:
        closes = [e for e in position.events if e.kind is not EventKind.OPEN]
        inside = [e for e in closes if start <= market_date(e.at) <= end]
        if not inside:
            continue
        closed = position.status is Status.CLOSED and start <= market_date(closes[-1].at) <= end
        found.append(TradePL(position, sum((e.realized for e in inside), ZERO), closed))
    return found


@dataclass(frozen=True)
class Cell:
    day: date
    in_month: bool
    pl: DayPL | None
    shade: str  # "gain-1".."gain-3", "loss-1".."loss-3", or "" for none


@dataclass
class Week:
    days: list[Cell]  # Monday to Friday
    weekend: Decimal = ZERO  # P/L booked on the Saturday or Sunday after
    total: Decimal = ZERO
    trades: int = 0
    start: date | None = None  # the week's first and last day in the month (its drawer)
    end: date | None = None


@dataclass
class MonthGrid:
    year: int
    month: int
    weeks: list[Week] = field(default_factory=list)
    days: list[DayPL] = field(default_factory=list)  # the month's active days

    @property
    def first(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last(self) -> date:
        return date(self.year, self.month, monthrange(self.year, self.month)[1])

    @property
    def total(self) -> Decimal:
        return sum((d.realized for d in self.days), ZERO)

    @property
    def trades(self) -> int:
        return sum(d.trades for d in self.days)

    @property
    def partials(self) -> int:
        return sum(d.partials for d in self.days)

    @property
    def winning_days(self) -> int:
        return sum(1 for d in self.days if d.realized > 0)

    @property
    def losing_days(self) -> int:
        return sum(1 for d in self.days if d.realized < 0)

    @property
    def average_day(self) -> Decimal | None:
        return self.total / len(self.days) if self.days else None

    @property
    def best_day(self) -> Decimal | None:
        return max((d.realized for d in self.days), default=None)

    @property
    def worst_day(self) -> Decimal | None:
        return min((d.realized for d in self.days), default=None)


def month_grid(year: int, month: int, days: dict[date, DayPL]) -> MonthGrid:
    """The month as weeks of Monday-Friday cells, each with its day's P/L."""
    grid = MonthGrid(year, month)
    grid.days = [
        pl for day, pl in days.items() if (day.year, day.month) == (year, month) and pl.active
    ]
    best = max((d.realized for d in grid.days), default=ZERO)
    worst = min((d.realized for d in grid.days), default=ZERO)

    for week in Calendar().monthdatescalendar(year, month):  # Monday first
        if not any(day.month == month for day in week[:WEEKDAYS]) and not any(
            day.month == month and day in days for day in week
        ):
            continue  # only a weekend of the month, with nothing booked on it
        inside = [day for day in week if day.month == month]
        row = Week(days=[], start=inside[0], end=inside[-1])
        for day in week:
            in_month = day.month == month
            pl = days.get(day) if in_month else None
            if pl is not None:
                row.total += pl.realized
                row.trades += pl.trades
                if day.weekday() >= WEEKDAYS:
                    row.weekend += pl.realized
            if day.weekday() < WEEKDAYS:
                row.days.append(Cell(day, in_month, pl, _shade(pl, best, worst)))
        grid.weeks.append(row)
    return grid


def _shade(pl: DayPL | None, best: Decimal, worst: Decimal) -> str:
    if pl is None or pl.realized == 0:
        return ""
    if pl.realized > 0:
        name, ratio = "gain", pl.realized / best
    else:
        name, ratio = "loss", pl.realized / worst
    level = next(level for floor, level in SHADES if ratio >= floor)
    return f"{name}-{level}"


@dataclass(frozen=True)
class CurvePoint:
    day: date
    pl: Decimal  # realized that day
    cumulative: Decimal  # realized up to and including that day
    drawdown: Decimal  # how far below the highest cumulative so far (<= 0)


def equity_curve(days: dict[date, DayPL]) -> list[CurvePoint]:
    """Cumulative realized P/L and drawdown, one point per day with closes.
    The high-water mark starts at zero, as in Tracker2, so losses before the
    first gain count as drawdown."""
    curve, cumulative, peak = [], ZERO, ZERO
    for day in sorted(d for d, pl in days.items() if pl.active):
        cumulative += days[day].realized
        peak = max(peak, cumulative)
        curve.append(CurvePoint(day, days[day].realized, cumulative, cumulative - peak))
    return curve


def max_drawdown(curve: list[CurvePoint]) -> CurvePoint | None:
    """The point of the deepest drawdown, or None if there was none."""
    deepest = min(curve, key=lambda point: point.drawdown, default=None)
    return deepest if deepest is not None and deepest.drawdown < 0 else None


@dataclass(frozen=True)
class MonthRow:
    month: date  # the first of the month
    pl: Decimal
    cumulative: Decimal  # at the month's last day with closes
    drawdown: Decimal  # the deepest drawdown during the month


def by_month(curve: list[CurvePoint]) -> list[MonthRow]:
    """The curve summed up per month, oldest first."""
    rows: list[MonthRow] = []
    for point in curve:
        month = point.day.replace(day=1)
        if rows and rows[-1].month == month:
            last = rows[-1]
            rows[-1] = MonthRow(
                month, last.pl + point.pl, point.cumulative, min(last.drawdown, point.drawdown)
            )
        else:
            rows.append(MonthRow(month, point.pl, point.cumulative, point.drawdown))
    return rows
