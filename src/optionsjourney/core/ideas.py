"""Trade Ideas rules (pure, #292): checking a saved ticket and working out its P/L.

An idea holds one card, the Trade ticket as saved:
{"label", "qty", "entry_unit" (credit +, debit -), "locked", "multiplier",
 "legs": [{"symbol": OCC, "side": "buy"|"sell", "ratio"}]}.
A card's "unit" is its price per share on the same sign: a sold leg counts +, a bought
leg -, each times its ratio. P/L is (entry_unit - current unit) x qty x multiplier."""

import math
from datetime import date, datetime
from decimal import Decimal

from optionsjourney.core.expiry import intrinsic_value
from optionsjourney.core.market import expiry_close
from optionsjourney.core.symbols import OCC, normalize_underlying, parse_occ

MAX_LEGS = 8
LABEL_MAX = 80
_NO_IDEAS_ON = {"VIX", "VIXW"}


class IdeaRefused(Exception):
    """The ticket can't be saved as an idea; the message says why."""


def _padded(symbol: str) -> str:
    """The OCC symbol the quote hub keys on: root padded to 6 characters."""
    root, yymmdd, put_call, strike = OCC.match(symbol.strip()).groups()
    return f"{root:<6}{yymmdd}{put_call}{strike}"


def _whole(value) -> int:
    """A whole number; 2.7 is refused, not cut to 2."""
    number = float(value)
    if isinstance(value, bool) or not number.is_integer():
        raise ValueError(value)
    return int(number)


def _clean_leg(leg: dict, ticker: str) -> dict:
    symbol = str(leg.get("symbol") or "").strip()
    if symbol.startswith("./"):
        raise IdeaRefused("Futures options can't be saved as ideas yet.")
    parsed = parse_occ(symbol)
    if parsed is None:
        raise IdeaRefused(f"{symbol or 'A leg'} isn't an option symbol.")
    if parsed[0] in _NO_IDEAS_ON or OCC.match(symbol).group(1) in _NO_IDEAS_ON:
        raise IdeaRefused("VIX options can't be saved as ideas.")
    if normalize_underlying(ticker) != parsed[0]:
        raise IdeaRefused(f"{symbol} isn't an option on {ticker}.")
    if leg.get("side") not in ("buy", "sell"):
        raise IdeaRefused("Each leg must be a buy or a sell.")
    try:
        ratio = _whole(leg.get("ratio", 1))
    except TypeError, ValueError:
        raise IdeaRefused("A leg's ratio must be a whole number.") from None
    if ratio < 1:
        raise IdeaRefused("A leg's ratio must be at least 1.")
    return {"symbol": _padded(symbol), "side": leg["side"], "ratio": ratio}


def validate(ticker: str, label: str, cards: list) -> list[dict]:
    """The cleaned cards, or IdeaRefused."""
    if not (label or "").strip():
        raise IdeaRefused("Give the idea a name.")
    if not isinstance(cards, list) or len(cards) != 1:
        raise IdeaRefused("An idea holds exactly one trade.")
    card = cards[0]
    legs = (card.get("legs") or []) if isinstance(card, dict) else None
    if not isinstance(legs, list) or not all(isinstance(leg, dict) for leg in legs):
        raise IdeaRefused("That isn't a trade from the ticket.")
    if not 1 <= len(legs) <= MAX_LEGS:
        raise IdeaRefused(f"A trade needs 1 to {MAX_LEGS} legs.")
    try:
        qty = _whole(card.get("qty", 0))
        entry = float(card.get("entry_unit"))
        multiplier = _whole(card.get("multiplier", 100))
    except TypeError, ValueError:
        raise IdeaRefused("The quantity, multiplier and price must be numbers.") from None
    if qty < 1:
        raise IdeaRefused("The quantity must be at least 1.")
    if multiplier < 1:
        raise IdeaRefused("The multiplier must be at least 1.")
    if not math.isfinite(entry):
        raise IdeaRefused("The price must be a number.")
    return [
        {
            "label": (str(card.get("label") or "").strip() or label.strip())[:LABEL_MAX],
            "qty": qty,
            "entry_unit": entry,
            "locked": bool(card.get("locked", False)),
            "multiplier": multiplier,
            "legs": [_clean_leg(leg, ticker) for leg in legs],
        }
    ]


def _expirations(card: dict) -> list[date]:
    return [parse_occ(leg["symbol"])[1].expiration for leg in card["legs"]]


def has_expired(expiration: date, now: datetime) -> bool:
    """A leg is live until its expiration day's close."""
    return now >= expiry_close(expiration)


def status(cards: list[dict], now: datetime) -> str:
    """ "live", "partly_expired" or "expired" across every leg of every card."""
    gone = [has_expired(day, now) for card in cards for day in _expirations(card)]
    if all(gone):
        return "expired"
    return "partly_expired" if any(gone) else "live"


def card_pnl(card: dict, current_unit: Decimal) -> Decimal:
    entry = Decimal(str(card["entry_unit"]))
    return (entry - current_unit) * card["qty"] * card["multiplier"]


def _unit(card: dict, price_of) -> Decimal | None:
    total = Decimal(0)
    for leg in card["legs"]:
        price = price_of(leg)
        if price is None:
            return None
        sign = 1 if leg["side"] == "sell" else -1
        total += sign * leg["ratio"] * price
    return total


def card_unit(card: dict, marks: dict[str, Decimal]) -> Decimal | None:
    """The card's current unit from per-leg prices; None if one is missing."""
    return _unit(card, lambda leg: marks.get(leg["symbol"]))


def settled_unit(card: dict, closes: dict[tuple[str, date], Decimal]) -> Decimal | None:
    """The card's unit at expiry, from each leg's intrinsic value at its underlying's
    close; None if a close is missing."""

    def intrinsic(leg):
        underlying, option = parse_occ(leg["symbol"])
        close = closes.get((underlying, option.expiration))
        return None if close is None else intrinsic_value(option, close)

    return _unit(card, intrinsic)


def pnl_percent(pnl: Decimal, card: dict) -> float | None:
    """P/L as a percent of the money the trade moved at entry."""
    basis = abs(Decimal(str(card["entry_unit"])) * card["qty"] * card["multiplier"])
    return float(pnl / basis * 100) if basis else None
