"""Recognizing what a set of legs is: "Iron Condor", "Put Credit Spread", ...

Legs are described as the position they make: + long, - short. A closing
order is therefore named after the position it closes, not the trades it took
(buying back a put credit spread is still a "Put Credit Spread")."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from optionsjourney.core.transactions import InstrumentType, OptionDetails


@dataclass(frozen=True)
class Leg:
    symbol: str
    instrument_type: InstrumentType
    option: OptionDetails | None
    quantity: Decimal  # position: + long, - short


def describe(legs: Sequence[Leg]) -> str:
    """The structure's name, or "Custom (N legs)" when it isn't a known shape."""
    legs = [leg for leg in legs if leg.quantity != 0]
    options = [leg for leg in legs if leg.option is not None]
    others = [leg for leg in legs if leg.option is None]

    if not options:
        if len(others) == 1:
            return f"{_long_short(others[0])} {_instrument_word(others[0])}"
        return _custom(legs)
    if others:
        return _with_stock(others, options) or _custom(legs)

    match len(options):
        case 1:
            leg = options[0]
            return f"{_long_short(leg)} {leg.option.put_call.title()}"
        case 2:
            return _two_legs(*options) or _custom(legs)
        case 3:
            return _butterfly(options) or _custom(legs)
        case 4:
            return _four_legs(options) or _custom(legs)
    return _custom(legs)


def splits_by_type(legs: Sequence[Leg]) -> bool:
    """#166: a sold and a bought put and a sold and a bought call, all with the same
    expiration, in any quantities (Tracker2 splits any such four legs): the journal can
    split it into a put spread and a call spread. An iron butterfly is one too."""
    legs = [leg for leg in legs if leg.quantity != 0]
    if len(legs) != 4 or any(leg.option is None for leg in legs):
        return False
    if len({leg.option.expiration for leg in legs}) != 1:
        return False
    pairs = sorted((leg.option.put_call, leg.quantity > 0) for leg in legs)
    return pairs == [("CALL", False), ("CALL", True), ("PUT", False), ("PUT", True)]


def _two_legs(a: Leg, b: Leg) -> str | None:
    kind = a.option.put_call.title()
    same_type = a.option.put_call == b.option.put_call
    same_expiration = a.option.expiration == b.option.expiration
    opposite = (a.quantity > 0) != (b.quantity > 0)

    if same_type and same_expiration and opposite and a.option.strike != b.option.strike:
        if abs(a.quantity) != abs(b.quantity):
            return f"{kind} Ratio Spread"
        return f"{kind} {'Credit' if _is_credit_vertical(a, b) else 'Debit'} Spread"
    if same_type and not same_expiration and opposite:
        return f"{kind} {'Calendar' if a.option.strike == b.option.strike else 'Diagonal'}"
    if not same_type and same_expiration and not opposite and abs(a.quantity) == abs(b.quantity):
        shape = "Straddle" if a.option.strike == b.option.strike else "Strangle"
        return f"{_long_short(a)} {shape}"
    return None


def _is_credit_vertical(a: Leg, b: Leg) -> bool:
    """A vertical is a credit spread when its short leg is nearer the money:
    the higher strike for puts, the lower strike for calls."""
    short, long_ = (a, b) if a.quantity < 0 else (b, a)
    if short.option.put_call == "PUT":
        return short.option.strike > long_.option.strike
    return short.option.strike < long_.option.strike


def _butterfly(legs: list[Leg]) -> str | None:
    if len({leg.option.put_call for leg in legs}) != 1:
        return None
    if len({leg.option.expiration for leg in legs}) != 1:
        return None
    low, body, high = sorted(legs, key=lambda leg: leg.option.strike)
    if not (low.option.strike < body.option.strike < high.option.strike):
        return None
    wing = low.quantity
    if high.quantity != wing or body.quantity != -2 * wing:
        return None
    kind = low.option.put_call.title()
    lower_width = body.option.strike - low.option.strike
    upper_width = high.option.strike - body.option.strike
    return f"{kind} {'Butterfly' if lower_width == upper_width else 'Broken Wing Butterfly'}"


def _four_legs(legs: list[Leg]) -> str | None:
    puts = [leg for leg in legs if leg.option.put_call == "PUT"]
    calls = [leg for leg in legs if leg.option.put_call == "CALL"]
    if len(puts) != 2 or len(calls) != 2:
        return None
    if len({abs(leg.quantity) for leg in legs}) != 1:
        return None

    put_shape, call_shape = _two_legs(*puts), _two_legs(*calls)
    if put_shape is None or call_shape is None:
        return None

    time_spreads = ("Calendar", "Diagonal")
    if put_shape.endswith(time_spreads) and call_shape.endswith(time_spreads):
        both_calendars = put_shape.endswith("Calendar") and call_shape.endswith("Calendar")
        return "Double Calendar" if both_calendars else "Double Diagonal"

    if len({leg.option.expiration for leg in legs}) != 1:
        return None
    put_credit = put_shape == "Put Credit Spread"
    call_credit = call_shape == "Call Credit Spread"
    if put_credit != call_credit:
        return None
    short_strikes = {leg.option.strike for leg in legs if (leg.quantity < 0) == put_credit}
    shape = "Iron Butterfly" if len(short_strikes) == 1 else "Iron Condor"
    return shape if put_credit else f"Reverse {shape}"


def _with_stock(stock: list[Leg], options: list[Leg]) -> str | None:
    if len(stock) != 1 or len(options) != 1:
        return None
    shares, option = stock[0], options[0]
    if shares.quantity > 0 and option.quantity < 0 and option.option.put_call == "CALL":
        return "Covered Call"
    if shares.quantity > 0 and option.quantity > 0 and option.option.put_call == "PUT":
        return "Protective Put"
    return None


def _long_short(leg: Leg) -> str:
    return "Long" if leg.quantity > 0 else "Short"


def _instrument_word(leg: Leg) -> str:
    return "Future" if leg.instrument_type is InstrumentType.FUTURE else "Stock"


def _custom(legs: Sequence[Leg]) -> str:
    return f"Custom ({len(legs)} legs)"
