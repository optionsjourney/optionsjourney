"""What each row of the journal's trade list shows (#61), as Tracker2's rows:
the legs still open (each priced live on the page), the closing fills,
the net premium, fees split into opening and closing, and P/L as a
percentage of the premium."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.positions import EventKind, Position, Status
from optionsjourney.core.stats import premium_of
from optionsjourney.core.transactions import Effect, OptionDetails, Side

ZERO = Decimal("0")


@dataclass(frozen=True)
class LegLine:
    quantity: Decimal  # + bought, - sold
    label: str  # "5800 P 10/16", or "shares"
    symbol: str  # the quote key, for the live mark
    price: Decimal | None = None  # a closing fill's price


@dataclass(frozen=True)
class ClosingOrder:
    """One closing order's fills (Tracker2's Closing Legs column groups by order)."""

    at: datetime
    kind: str  # "close", "roll", "expire", ...: the event it belongs to
    legs: list[LegLine]


@dataclass(frozen=True)
class RowDetails:
    open_legs: list[LegLine]
    closing: list[LegLine]
    premium: Decimal  # dollars, always positive
    credit: bool
    fees_open: Decimal
    fees_close: Decimal
    realized_percent: Decimal | None  # of the premium, once something closed
    closing_orders: tuple[ClosingOrder, ...] = ()


def _label(option: OptionDetails | None) -> str:
    if option is None:
        return "shares"
    strike = f"{option.strike.normalize():f}"
    return f"{strike} {option.put_call[0]} {option.expiration:%m/%d}"


def _by_strike(option: OptionDetails | None) -> tuple[bool, Decimal]:
    """Highest strike first, shares last (Tracker2 f55b846a)."""
    return (option is None, -option.strike if option is not None else ZERO)


def row_details(position: Position) -> RowDetails:
    open_legs = [
        LegLine(leg.sign * leg.remaining, _label(leg.option), leg.symbol)
        for leg in sorted(position.live_legs, key=lambda leg: _by_strike(leg.option))
        if leg.remaining > 0
    ]
    closing = []
    orders = []
    for event in position.events:
        if event.kind in (EventKind.OPEN, EventKind.ADD):
            continue
        for order in event.orders:
            legs = []
            for fill in sorted(order.fills, key=lambda fill: _by_strike(fill.option)):
                if fill.effect is Effect.OPEN:
                    continue  # a roll's new legs are open legs
                sign = 1 if fill.side is Side.BUY else -1
                legs.append(
                    LegLine(sign * fill.quantity, _label(fill.option), fill.symbol, fill.price)
                )
            if legs:
                closing.extend(legs)
                orders.append(ClosingOrder(event.at, event.kind.value, legs))
    opened = sum(
        (e.value for e in position.events if e.kind in (EventKind.OPEN, EventKind.ROLL)), ZERO
    )
    premium = premium_of(position)
    return RowDetails(
        open_legs=open_legs,
        closing=closing,
        premium=premium,
        credit=opened > 0,
        fees_open=sum((leg.open_fees for leg in position.legs), ZERO),
        fees_close=sum((leg.close_fees for leg in position.legs), ZERO),
        realized_percent=realized_percent(position),
        closing_orders=tuple(orders),
    )


def realized_percent(position: Position) -> Decimal | None:
    """P/L as a % of the premium taken in or paid, once something closed (also the bot
    record's P/L %, bots slice 20; Tracker2's _trade_row divides by the same amount for
    a trade that wasn't rolled)."""
    premium = premium_of(position)
    if not premium or position.status is Status.OPEN:
        return None
    return position.realized_pl / premium * 100


def days_in_trade(position: Position, now: datetime | None = None) -> int | None:
    """New York days from the open to the close (Tracker2's _trade_row: closed date −
    opened date; a same-day trade is 0). Still open: the days so far when `now` is given,
    else None."""
    end = position.closed_at if position.status is Status.CLOSED else now
    if end is None or position.opened_at is None:
        return None
    return (end.astimezone(MARKET_TZ).date() - position.opened_at.astimezone(MARKET_TZ).date()).days
