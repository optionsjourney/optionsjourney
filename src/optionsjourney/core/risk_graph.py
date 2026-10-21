"""A journal trade as the risk graph's legs: Tracker2's chart-data leg shape
(journal_bp._chart_data_for), which its risk_graph.js and toModelerLeg read.

entry_price is per contract, + received / - paid, averaged over what was
opened; quantity is what's still open. Futures options carry their own
multiplier and no stream symbol (the risk-quote socket takes DXLink option
symbols only), so they graph at their opening snapshot."""

from collections.abc import Mapping
from decimal import Decimal

from optionsjourney.core.positions import Position
from optionsjourney.core.quotes import dxlink_symbol
from optionsjourney.core.symbols import normalize_underlying
from optionsjourney.core.transactions import InstrumentType

OPTION_MULTIPLIER = Decimal("100")
_OPTIONS = (InstrumentType.OPTION, InstrumentType.FUTURE_OPTION)


def graph_legs(
    position: Position, multipliers: Mapping[str, Decimal], marks: Mapping[str, Decimal]
) -> list[dict]:
    legs = []
    for leg in position.live_legs:
        if leg.instrument_type not in _OPTIONS or leg.option is None or leg.remaining <= 0:
            continue
        futures = leg.instrument_type is InstrumentType.FUTURE_OPTION
        multiplier = multipliers.get(leg.symbol, OPTION_MULTIPLIER)
        mark = marks.get(leg.symbol)
        legs.append(
            {
                "leg_type": leg.option.put_call,
                "side": "LONG" if leg.sign > 0 else "SHORT",
                "strike": float(leg.option.strike),
                "expiration_date": leg.option.expiration.isoformat(),
                "entry_price": float(leg.open_value / (leg.opened * multiplier)),
                "quantity": int(leg.remaining),
                "multiplier": int(multiplier),
                "live_price": None if mark is None else float(mark),
                "stream_symbol": None if futures else dxlink_symbol(leg.symbol),
            }
        )
    return legs


def graph_symbol(position: Position) -> str:
    """The underlying the graph prices against and groups by: "SPX" for SPXW
    legs, and a futures option's own contract ("/ESH7"), like Tracker2's
    underlying_symbol: two months of /ES are two prices."""
    underlying = (position.underlying or "").strip().upper().removeprefix(".")
    if underlying.startswith("/"):
        return underlying
    return normalize_underlying(underlying)
