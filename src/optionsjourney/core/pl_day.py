"""P/L Day (#51): how much a trade made or lost today. Ported from Tracker2's
services/pl_day.py.

P/L Day = the trade's total P/L now (realized + open at the live marks) minus
its total P/L at the baseline. The baseline is, in order:

1. Last night's snapshot of its total (services/pl_day.py takes one after
   each close).
2. 0 for a trade opened today.
3. Its own total for a trade finished before today, so it contributes 0.
4. Otherwise rebuilt from the brokers' previous closes, which is approximate.

Taking whole totals means rolls, partial closes and expirations need no
special case: a close today counts its fill against last night's value."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from optionsjourney.core.live import MULTIPLIERS, unrealized_pl
from optionsjourney.core.market import MARKET_TZ, market_date
from optionsjourney.core.positions import Position, Status
from optionsjourney.core.sessions import session_close
from optionsjourney.core.transactions import Effect

ZERO = Decimal("0")


def schwab_close_counts(today: date, now: datetime) -> bool:
    """Schwab's closePrice is the previous session's close only during the
    trading day, before today's 4:00 PM close; after it, it may be today's."""
    close = session_close(today)
    if market_date(now) != today or close is None:
        return False
    return now < datetime.combine(today, close, MARKET_TZ)


@dataclass(frozen=True)
class PLDay:
    value: Decimal | None  # None: an open leg has no price, or no baseline
    approximate: bool = False  # rebuilt from previous closes, not a snapshot


def total_pl(
    position: Position,
    marks: Mapping[str, Decimal],
    multipliers: Mapping[str, Decimal] | None = None,
) -> Decimal | None:
    """Realized plus open P/L; None when an open leg has no mark."""
    if position.status is Status.CLOSED:
        return position.realized_pl
    unrealized = unrealized_pl(position, marks, multipliers)
    return None if unrealized is None else position.realized_pl + unrealized


def _moved_today(position: Position, today) -> tuple[dict, dict, Decimal]:
    """Per leg symbol: contracts opened today and closed today; and the P/L
    realized today."""
    opened: dict[str, Decimal] = {}
    closed: dict[str, Decimal] = {}
    realized = ZERO
    for event in position.events:
        if market_date(event.at) != today:
            continue
        realized += event.realized
        for order in event.orders:
            for fill in order.fills:
                into = opened if fill.effect is Effect.OPEN else closed
                into[fill.symbol] = into.get(fill.symbol, ZERO) + fill.quantity
    return opened, closed, realized


def _rebuilt_baseline(
    position: Position,
    today,
    prev_closes: Mapping[str, Decimal],
    multipliers: Mapping[str, Decimal] | None,
) -> Decimal | None:
    """Last night's total from the previous closes: what was realized before
    today, plus what was held last night valued at those closes."""
    opened_today, closed_today, realized_today = _moved_today(position, today)
    total = position.realized_pl - realized_today
    for leg in position.live_legs + [leg for leg in position.legs if leg.rolled]:
        held = (
            leg.remaining + closed_today.get(leg.symbol, ZERO) - opened_today.get(leg.symbol, ZERO)
        )
        if held <= 0:
            continue
        multiplier = MULTIPLIERS.get(leg.instrument_type) or (multipliers or {}).get(leg.symbol)
        close = prev_closes.get(leg.symbol)
        if multiplier is None or close is None:
            return None
        total += (leg.open_value - leg.open_fees) * held / leg.opened
        total += leg.sign * close * held * multiplier
    return total


def pl_day(
    position: Position,
    marks: Mapping[str, Decimal],
    today,
    snapshot: Decimal | None,
    prev_closes: Mapping[str, Decimal],
    multipliers: Mapping[str, Decimal] | None = None,
) -> PLDay:
    finished_before = (
        position.status is Status.CLOSED
        and position.closed_at is not None
        and market_date(position.closed_at) < today
    )
    if finished_before:
        return PLDay(ZERO)
    now = total_pl(position, marks, multipliers)
    if now is None:
        return PLDay(None)
    if snapshot is not None:
        return PLDay(now - snapshot)
    if market_date(position.opened_at) >= today:
        return PLDay(now)
    baseline = _rebuilt_baseline(position, today, prev_closes, multipliers)
    return PLDay(None) if baseline is None else PLDay(now - baseline, approximate=True)
