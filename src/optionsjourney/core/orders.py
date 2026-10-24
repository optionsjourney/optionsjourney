"""Grouping fills into orders.

All the legs of one broker order belong together. Within an order, opens and
closes are kept apart, so a roll shows as what it closed and what it opened.
Expirations, assignments and exercises carry no order ID; the legs of one
position expire together, so they're grouped by account, time and underlying."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from optionsjourney.core.structures import Leg, describe
from optionsjourney.core.transactions import Effect, Fill, Kind, Side, instrument_label


@dataclass(frozen=True)
class OrderGroup:
    broker: str
    account_number: str
    order_id: str | None
    kind: Kind
    effect: Effect | None
    fills: tuple[Fill, ...]
    legs: tuple[Leg, ...]

    @property
    def executed_at(self) -> datetime:
        return min(fill.executed_at for fill in self.fills)

    @property
    def underlying(self) -> str:
        underlyings = {fill.underlying for fill in self.fills}
        return underlyings.pop() if len(underlyings) == 1 else "Multiple"

    @property
    def structure(self) -> str:
        return describe(self.legs)

    @property
    def name(self) -> str:
        """The default trade name, e.g. "SPX Iron Condor"."""
        return f"{self.underlying} {self.structure}"

    @property
    def action_label(self) -> str:
        if self.kind is not Kind.TRADE:
            return self.kind.value.title()
        return {Effect.OPEN: "Open", Effect.CLOSE: "Close"}.get(self.effect, "Trade")

    @property
    def value(self) -> Decimal:
        return sum((fill.value for fill in self.fills), Decimal("0"))

    @property
    def fees(self) -> Decimal:
        return sum((fill.fees for fill in self.fills), Decimal("0"))

    @property
    def search_text(self) -> str:
        """What the Transactions search looks in: the name, action, legs and IDs."""
        legs = [self.leg_label(leg) for leg in self.legs]
        return " ".join(
            [
                self.name,
                self.action_label,
                *legs,
                self.order_id or "",
                *(fill.search_text for fill in self.fills),
            ]
        )

    def leg_label(self, leg: Leg) -> str:
        """e.g. "-1 SPX 10/09/26 5800 Put"."""
        underlying = next(f.underlying for f in self.fills if f.symbol == leg.symbol)
        quantity = f"{leg.quantity.normalize():+f}"
        return f"{quantity} {instrument_label(underlying, leg.option, leg.symbol)}"


def group_fills(fills: Iterable[Fill]) -> list[OrderGroup]:
    """Fills grouped into orders, newest first."""
    groups: dict[tuple, list[Fill]] = {}
    for fill in fills:
        groups.setdefault(_key(fill), []).append(fill)

    orders = [
        OrderGroup(
            broker=members[0].broker,
            account_number=members[0].account_number,
            order_id=members[0].order_id,
            kind=members[0].kind,
            effect=members[0].effect,
            fills=tuple(sorted(members, key=lambda f: (f.executed_at, f.transaction_id, f.leg))),
            legs=legs_of(members),
        )
        for members in groups.values()
    ]
    return sorted(orders, key=lambda order: order.executed_at, reverse=True)


def _key(fill: Fill) -> tuple:
    if fill.order_id is not None:
        return (fill.broker, fill.account_number, "order", fill.order_id, fill.kind, fill.effect)
    return (
        fill.broker,
        fill.account_number,
        fill.kind,
        fill.effect,
        fill.executed_at,
        fill.underlying,
    )


def legs_of(fills: Iterable[Fill]) -> tuple[Leg, ...]:
    """One leg per instrument, partial fills added together, as a position."""
    quantities: dict[str, Decimal] = {}
    first: dict[str, Fill] = {}
    for fill in fills:
        first.setdefault(fill.symbol, fill)
        quantities[fill.symbol] = quantities.get(fill.symbol, Decimal("0")) + _position(fill)
    return tuple(
        Leg(
            symbol=symbol,
            instrument_type=first[symbol].instrument_type,
            option=first[symbol].option,
            quantity=quantity,
        )
        for symbol, quantity in quantities.items()
    )


def _position(fill: Fill) -> Decimal:
    """The position this fill opens (or, for a close, the one it closes):
    + long, - short."""
    if fill.side is None:
        return Decimal("0")
    bought = fill.side is Side.BUY
    closing = fill.effect is Effect.CLOSE or (fill.kind is not Kind.TRADE and fill.effect is None)
    long_ = bought != closing
    return fill.quantity if long_ else -fill.quantity
