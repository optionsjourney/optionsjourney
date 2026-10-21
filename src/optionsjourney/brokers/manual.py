"""Trades entered by hand (docs/decisions/0023): for trades the app can't
sync, such as at a broker it doesn't connect.

An entry is stored like a broker transaction (broker "manual"), as JSON this
module writes (`build_entry`) and reads back into fills (`parse_transaction`).
So manual trades go through the same orders -> positions -> P/L pipeline as
synced ones and show up everywhere they do.

    {"order_id": "...", "executed_at": "2026-09-01T14:00:00+00:00",
     "effect": "open" | "close" | "expire", "fees": "2.60",
     "legs": [{"type": "put" | "call" | "shares", "side": "buy" | "sell",
               "quantity": "1", "underlying": "SPX", "strike": "5800",
               "expiration": "2026-10-16", "price": "2.00"}, ...]}

Options are priced per share with the standard 100 multiplier. Futures
options aren't supported by hand yet."""

import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from optionsjourney.core.symbols import normalize_underlying, occ_symbol
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
)

BROKER = "manual"
TRANSACTION_TYPE = "Manual"
OPTION_MULTIPLIER = Decimal("100")
EFFECTS = ("open", "close", "expire")
LEG_TYPES = ("put", "call", "shares")
MAX_LEGS = 4
ZERO = Decimal("0")


class ManualEntryError(ValueError):
    """An entry the app can't accept. The message says what to fix."""


def build_entry(*, executed_at: datetime, effect: str, fees: str, legs: list[dict]) -> dict:
    """A checked, normalized entry, ready to store. Raises ManualEntryError."""
    if effect not in EFFECTS:
        raise ManualEntryError("Choose open, close or expire.")
    if executed_at.tzinfo is None:
        raise ManualEntryError("The date and time need a time zone.")
    if not legs:
        raise ManualEntryError("Add at least one leg.")
    if len(legs) > MAX_LEGS:
        raise ManualEntryError(f"An entry can have at most {MAX_LEGS} legs.")
    return {
        "order_id": uuid.uuid4().hex,
        "executed_at": executed_at.isoformat(),
        "effect": effect,
        "fees": str(_number(fees or "0", "Fees", allow_zero=True)),
        "legs": [_leg(leg, number, effect) for number, leg in enumerate(legs, start=1)],
    }


def _leg(leg: dict, number: int, effect: str) -> dict:
    where = f"Leg {number}"
    kind = (leg.get("type") or "").strip().lower()
    if kind not in LEG_TYPES:
        raise ManualEntryError(f"{where}: choose a type (put, call or shares).")
    side = (leg.get("side") or "").strip().lower()
    if side not in ("buy", "sell"):
        raise ManualEntryError(f"{where}: choose buy or sell.")
    underlying = (leg.get("underlying") or "").strip().upper()
    if not underlying:
        raise ManualEntryError(f"{where}: enter the underlying symbol, e.g. SPX.")
    clean = {
        "type": kind,
        "side": side,
        "quantity": str(_number(leg.get("quantity"), f"{where}: quantity")),
        "underlying": underlying,
        "price": str(_number(leg.get("price") or "0", f"{where}: price", allow_zero=True)),
    }
    if kind != "shares":
        clean["strike"] = str(_number(leg.get("strike"), f"{where}: strike"))
        try:
            clean["expiration"] = date.fromisoformat(
                (leg.get("expiration") or "").strip()
            ).isoformat()
        except ValueError:
            raise ManualEntryError(f"{where}: enter the expiration as a date.") from None
    elif effect == "expire":
        raise ManualEntryError(f"{where}: shares don't expire.")
    return clean


def _number(text, label: str, allow_zero: bool = False) -> Decimal:
    try:
        value = Decimal(str(text).strip().replace(",", "").replace("$", ""))
    except InvalidOperation, ValueError:
        raise ManualEntryError(f"{label} must be a number.") from None
    if value < 0 or (value == 0 and not allow_zero) or not value.is_finite():
        raise ManualEntryError(
            f"{label} must be more than zero." if not allow_zero else f"{label} can't be negative."
        )
    return value


def parse_transaction(raw: dict, account_number: str) -> list[Fill]:
    """The fills in one manual entry: one per leg, fees on the first."""
    effect = raw["effect"]
    kind = Kind.EXPIRATION if effect == "expire" else Kind.TRADE
    executed_at = datetime.fromisoformat(raw["executed_at"])
    fills = []
    for number, leg in enumerate(raw["legs"]):
        side = Side.SELL if leg["side"] == "sell" else Side.BUY
        quantity, price = Decimal(leg["quantity"]), Decimal(leg["price"])
        underlying = normalize_underlying(leg["underlying"])
        if leg["type"] == "shares":
            instrument, option, symbol, multiplier = (
                InstrumentType.STOCK,
                None,
                leg["underlying"],
                Decimal("1"),
            )
        else:
            instrument, multiplier = InstrumentType.OPTION, OPTION_MULTIPLIER
            option = OptionDetails(
                put_call=leg["type"].upper(),
                strike=Decimal(leg["strike"]),
                expiration=date.fromisoformat(leg["expiration"]),
            )
            symbol = occ_symbol(leg["underlying"], option)
        value = ZERO if kind is Kind.EXPIRATION else price * quantity * multiplier
        fills.append(
            Fill(
                broker=BROKER,
                account_number=account_number,
                transaction_id=raw["order_id"],
                leg=number,
                executed_at=executed_at,
                kind=kind,
                instrument_type=instrument,
                symbol=symbol,
                underlying=underlying,
                option=option,
                side=side,
                effect=Effect.OPEN if effect == "open" else Effect.CLOSE,
                quantity=quantity,
                price=None if kind is Kind.EXPIRATION else price,
                value=value if side is Side.SELL else -value,
                fees=Decimal(raw.get("fees", "0")) if number == 0 else ZERO,
                order_id=raw["order_id"],
                description="Entered by hand",
            )
        )
    return fills
