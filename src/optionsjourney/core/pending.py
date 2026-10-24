"""Pending orders (#292): which tab of the Pending drawer an order belongs to. Pure."""

from datetime import date

from optionsjourney.core.symbols import parse_occ
from optionsjourney.trading.orders import BrokerOrder


def category(found: BrokerOrder, today: date) -> str:
    """ "0dte" when every leg expires today, "calendars" when the legs expire on
    different dates, otherwise "options" (including symbols that don't parse)."""
    parsed = [parse_occ(leg.symbol) for leg in found.legs]
    if not parsed or any(item is None for item in parsed):
        return "options"
    expiries = {item[1].expiration for item in parsed}
    if expiries == {today}:
        return "0dte"
    return "calendars" if len(expiries) > 1 else "options"
