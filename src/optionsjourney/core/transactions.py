"""Broker transactions, and the fills they turn into.

A RawTransaction is a broker's record exactly as received. A Fill is one leg of
it in a common shape, the same for every broker. Brokers translate raw records
into fills (brokers/tastytrade.py, brokers/schwab.py); everything downstream
works on fills only."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Kind(StrEnum):
    TRADE = "trade"
    EXPIRATION = "expiration"
    ASSIGNMENT = "assignment"
    EXERCISE = "exercise"
    MATURITY = "maturity"  # a bond or Treasury bill paid out at maturity
    OTHER = "other"


class InstrumentType(StrEnum):
    OPTION = "option"
    FUTURE_OPTION = "future_option"
    STOCK = "stock"
    FUTURE = "future"
    OTHER = "other"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Effect(StrEnum):
    OPEN = "open"
    CLOSE = "close"


@dataclass(frozen=True)
class RawTransaction:
    """A broker transaction as received. `data` is the broker's JSON, untouched."""

    transaction_id: str
    executed_at: datetime
    transaction_type: str
    data: dict


@dataclass(frozen=True)
class OptionDetails:
    put_call: str  # "PUT" or "CALL"
    strike: Decimal
    expiration: date


@dataclass(frozen=True)
class Fill:
    broker: str
    account_number: str
    transaction_id: str
    leg: int
    executed_at: datetime  # timezone-aware
    kind: Kind
    instrument_type: InstrumentType
    symbol: str
    underlying: str
    option: OptionDetails | None
    side: Side | None
    effect: Effect | None
    quantity: Decimal
    price: Decimal | None  # per share or contract, unsigned
    value: Decimal  # cash effect: + received, - paid
    fees: Decimal  # total fees, + cost
    order_id: str | None = None
    description: str | None = None

    @property
    def instrument_label(self) -> str:
        return instrument_label(self.underlying, self.option, self.symbol)

    @property
    def action_label(self) -> str:
        """e.g. "Sell to Open", "Buy", "Expiration"."""
        if self.kind is not Kind.TRADE or self.side is None:
            return self.kind.value.title()
        if self.effect is None:
            return self.side.value.title()
        return f"{self.side.value.title()} to {self.effect.value.title()}"

    @property
    def search_text(self) -> str:
        """What the Transactions search looks in."""
        return " ".join(
            filter(
                None,
                [
                    self.instrument_label,
                    self.symbol,
                    self.action_label,
                    self.transaction_id,
                    self.order_id,
                    self.description,
                ],
            )
        )


def matches_search(text: str, query: str) -> bool:
    """True when every word of `query` is somewhere in `text`, ignoring case."""
    text = text.lower()
    return all(word in text for word in query.lower().split())


def instrument_label(underlying: str, option: OptionDetails | None, symbol: str) -> str:
    """e.g. "SPX 10/09/26 5800 Put", or the underlying for stock and futures."""
    if option is None:
        return underlying or symbol
    strike = f"{option.strike.normalize():f}"
    return f"{underlying} {option.expiration:%m/%d/%y} {strike} {option.put_call.title()}"
