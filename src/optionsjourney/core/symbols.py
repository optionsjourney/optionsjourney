"""Reading option details out of broker symbols."""

import re
from datetime import date
from decimal import Decimal

from optionsjourney.core.transactions import OptionDetails

# Weekly and PM-settled index roots trade as the same underlying.
_ROOT_ALIASES = {"SPXW": "SPX", "NDXP": "NDX", "RUTW": "RUT", "DJXW": "DJX", "VIXW": "VIX"}

# OCC: root (up to 6 chars, space-padded) + YYMMDD + C/P + strike x 1000 (8 digits)
OCC = re.compile(r"^([A-Z0-9.]{1,6})\s*(\d{6})([CP])(\d{8})$")

# Futures option description: "... 04/30/26 Put 6075.00 @ 44.25"
_FUTURES_DESCRIPTION = re.compile(r"(\d{2})/(\d{2})/(\d{2})\s+(Put|Call)\s+(\d+(?:\.\d+)?)", re.I)
# Futures option symbol ending: "...260430P6075"
_FUTURES_SYMBOL = re.compile(r"(\d{2})(\d{2})(\d{2})([CP])(\d+(?:\.\d+)?)$")


def normalize_underlying(root: str) -> str:
    """Weekly roots like SPXW and Schwab's $SPX all mean SPX."""
    root = (root or "").strip().upper().removeprefix("$")
    return _ROOT_ALIASES.get(root, root)


def parse_occ(symbol: str) -> tuple[str, OptionDetails] | None:
    """(underlying, details) from an OCC option symbol, or None."""
    match = OCC.match((symbol or "").strip())
    if not match:
        return None
    root, yymmdd, put_call, strike = match.groups()
    return normalize_underlying(root), OptionDetails(
        put_call="PUT" if put_call == "P" else "CALL",
        strike=Decimal(strike) / 1000,
        expiration=_yymmdd(yymmdd),
    )


def occ_symbol(root: str, option: OptionDetails) -> str:
    """The OCC symbol for an option, as brokers write it: "SPXW  251024P06600000"."""
    return (
        f"{root.strip().upper().removeprefix('$'):<6}{option.expiration:%y%m%d}"
        f"{option.put_call[0]}{int(option.strike * 1000):08d}"
    )


def parse_futures_option(description: str | None, symbol: str | None) -> OptionDetails | None:
    """Details of a futures option, from the description (most reliable) or the
    end of the symbol. Ported from Tracker2's _parse_futures_option_details."""
    match = _FUTURES_DESCRIPTION.search(description or "")
    if match:
        month, day, year, put_call, strike = match.groups()
        return OptionDetails(
            put_call=put_call.upper(),
            strike=Decimal(strike),
            expiration=date(2000 + int(year), int(month), int(day)),
        )
    match = _FUTURES_SYMBOL.search((symbol or "").replace(" ", ""))
    if match:
        year, month, day, put_call, strike = match.groups()
        return OptionDetails(
            put_call="PUT" if put_call == "P" else "CALL",
            strike=Decimal(strike),
            expiration=date(2000 + int(year), int(month), int(day)),
        )
    return None


def _yymmdd(text: str) -> date:
    return date(2000 + int(text[:2]), int(text[2:4]), int(text[4:6]))
