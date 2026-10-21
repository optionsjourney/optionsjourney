"""Account values over time: the accounts added up day by day, and how much
they gained once money moved in and out is taken away (docs/decisions/0023).

- Each account counts from its first recorded day. On a day it has no value
  (a Schwab day the app wasn't running), its last value stands.
- An account appearing (its first value) isn't a gain, and neither is a
  deposit: gain = end value - start value - money moved in - accounts joining.
- Money moved on a day without values (a weekend deposit) counts on the next
  day that has values."""

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from optionsjourney.core.accounts import DailyValue

ZERO = Decimal("0")


@dataclass(frozen=True)
class ValuePoint:
    day: date
    value: Decimal  # all the accounts, at the end of the day
    flow: Decimal = ZERO  # money moved in (+) or out (-) that day
    joined: int = 0  # accounts that started counting that day
    joined_value: Decimal = ZERO  # what they brought in


def combine(
    series: Mapping[Hashable, list[DailyValue]], flows: Mapping[date, Decimal]
) -> list[ValuePoint]:
    """The accounts' total per day, oldest first."""
    by_account = {
        key: {v.day: v.net_liquidating_value for v in values} for key, values in series.items()
    }
    first_day = {key: min(values) for key, values in by_account.items() if values}
    days = sorted({day for values in by_account.values() for day in values})
    pending_flows = sorted((day, amount) for day, amount in flows.items())

    points, last = [], {}
    for index, day in enumerate(days):
        for key, values in by_account.items():
            if day in values:
                last[key] = values[day]
        flow = ZERO
        while pending_flows and pending_flows[0][0] <= day:
            moved_on, amount = pending_flows.pop(0)
            if index > 0 or moved_on == day:
                flow += amount
        joining = [key for key, first in first_day.items() if first == day]
        points.append(
            ValuePoint(
                day=day,
                value=sum(last.values(), ZERO),
                flow=flow,
                joined=len(joining) if index else 0,
                joined_value=sum((by_account[k][day] for k in joining), ZERO) if index else ZERO,
            )
        )
    return points


@dataclass(frozen=True)
class Performance:
    start: Decimal
    end: Decimal
    net_flows: Decimal  # money moved in, less money moved out, after the start
    joined: Decimal  # value of accounts that started counting after the start

    @property
    def gain(self) -> Decimal:
        return self.end - self.start - self.net_flows - self.joined


def performance(points: list[ValuePoint]) -> Performance | None:
    if not points:
        return None
    later = points[1:]
    return Performance(
        start=points[0].value,
        end=points[-1].value,
        net_flows=sum((p.flow for p in later), ZERO),
        joined=sum((p.joined_value for p in later), ZERO),
    )


@dataclass(frozen=True)
class MonthRow:
    month: date  # the first of the month
    end: Decimal  # value at the month's last recorded day
    flows: Decimal  # money moved in or out during the month
    change: Decimal  # gain over the month, money moved and accounts joining taken out


def by_month(points: list[ValuePoint]) -> list[MonthRow]:
    """Month by month, oldest first. Each month's change is measured from the
    previous month's end (the first month's from the first day)."""
    rows: list[MonthRow] = []
    start = points[0].value if points else ZERO
    flows = joined = ZERO
    for index, point in enumerate(points):
        month = point.day.replace(day=1)
        if rows and rows[-1].month != month:
            start, flows, joined = rows[-1].end, ZERO, ZERO
        if index:
            flows += point.flow
            joined += point.joined_value
        row = MonthRow(month, point.value, flows, point.value - start - flows - joined)
        if rows and rows[-1].month == month:
            rows[-1] = row
        else:
            rows.append(row)
    return rows
