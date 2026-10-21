"""Open (unrealized) P/L from live marks (#24).

A trade's open P/L is what it took in (or paid) for what's still open, plus
what closing that now would pay (or cost) at the marks: per leg,
(open cash - open fees) x share still open + sign x mark x quantity left x
multiplier. Fees to close aren't known until the close, so they're left out."""

from collections.abc import Iterable, Mapping
from decimal import Decimal

from optionsjourney.core.positions import Position
from optionsjourney.core.transactions import Fill, InstrumentType

ZERO = Decimal("0")
MULTIPLIERS = {InstrumentType.OPTION: Decimal("100"), InstrumentType.STOCK: Decimal("1")}


def leg_keys(position: Position) -> set[str]:
    """The quote keys for the legs still open."""
    return {leg.symbol for leg in position.live_legs if leg.remaining > 0}


def multipliers_from_fills(fills: Iterable[Fill]) -> dict[str, Decimal]:
    """Dollars per point for futures options, from their own fills: cash /
    (price x quantity). Exact for any product (/ES 50, /GC 100, ...), with no
    lookups."""
    found: dict[str, Decimal] = {}
    for fill in fills:
        if (
            fill.instrument_type is InstrumentType.FUTURE_OPTION
            and fill.symbol not in found
            and fill.price
            and fill.quantity
            and fill.value
        ):
            found[fill.symbol] = (abs(fill.value) / (fill.price * fill.quantity)).quantize(
                Decimal("1")
            )
    return found


def unrealized_pl(
    position: Position,
    marks: Mapping[str, Decimal],
    multipliers: Mapping[str, Decimal] | None = None,
) -> Decimal | None:
    """None if any open leg has no mark, or no known multiplier (futures
    options need theirs from `multipliers`; futures aren't priced yet)."""
    total = ZERO
    for leg in position.live_legs:
        if leg.remaining <= 0:
            continue
        multiplier = MULTIPLIERS.get(leg.instrument_type) or (multipliers or {}).get(leg.symbol)
        mark = marks.get(leg.symbol)
        if multiplier is None or mark is None:
            return None
        share = leg.remaining / leg.opened
        total += (
            leg.open_value - leg.open_fees
        ) * share + leg.sign * mark * leg.remaining * multiplier
    return total
