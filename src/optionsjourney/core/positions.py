"""Building positions from orders: what was opened, how it was closed, P/L.

Follows Tracker2's rules (docs/decisions/0017-positions-follow-tracker2.md):

- An opening order starts a position. Its legs are the instruments it opened.
- A closing order (or a broker expiration, assignment or exercise) closes
  legs of the oldest open position in the same account that holds every one
  of its legs, in the same direction, with enough quantity left. A position
  can be closed in parts; it stays one position throughout.
- A roll is an order that closes whole legs of one position and opens new
  ones, where the order's quantities divide evenly into the position's size.
  The new legs join the same position: roll count goes up and the roll's net
  credit is added to the entry price. 0DTE positions are never rolled.
- Closes that match no position (opened before the synced history, say) are
  reported as unmatched, never guessed. So are broker events the app doesn't
  recognize (corporate actions, transfers): they're shown, not applied.
- A close is applied only if every one of its fills fits: the right
  instrument, the right direction, and no more than is left. A closing order
  that no single position fits may close legs of several (oldest first), but
  only if every contract in it is accounted for; Tracker2 left these for the
  user to match by hand.
- Expirations, assignments and exercises are settled leg by leg, not as one
  order: a broker reports everything expiring in an account at once, across
  many positions. Each leg settles the oldest positions holding it.
- An iron condor the user chose to split (docs/decisions/0018) opens as two
  positions, a put spread and a call spread. A closing order that covers both
  halves closes both: the one exception to "one close, one position".

P/L is computed from the brokers' own cash amounts, so contract multipliers
(100 for equity options, 50 for /ES options, ...) never have to be known."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from math import gcd

from optionsjourney.core.market import expiry_close, market_date
from optionsjourney.core.orders import OrderGroup, legs_of
from optionsjourney.core.structures import Leg, describe, splits_by_type
from optionsjourney.core.transactions import Effect, Fill, InstrumentType, Kind, OptionDetails, Side

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


class Status(StrEnum):
    OPEN = "open"
    PARTIAL = "partial"
    CLOSED = "closed"


class EventKind(StrEnum):
    OPEN = "open"
    ADD = "add"  # more shares bought into a running stock position (#45)
    CLOSE = "close"
    ROLL = "roll"
    EXPIRATION = "expiration"
    ASSIGNMENT = "assignment"
    EXERCISE = "exercise"
    MATURITY = "maturity"


_CLOSE_EVENTS = {
    Kind.TRADE: EventKind.CLOSE,
    Kind.EXPIRATION: EventKind.EXPIRATION,
    Kind.ASSIGNMENT: EventKind.ASSIGNMENT,
    Kind.EXERCISE: EventKind.EXERCISE,
    Kind.MATURITY: EventKind.MATURITY,
    Kind.OTHER: EventKind.CLOSE,
}

_CLOSE_REASONS = {
    EventKind.CLOSE: "Closed",
    EventKind.ROLL: "Closed",
    EventKind.EXPIRATION: "Expired",
    EventKind.ASSIGNMENT: "Assigned",
    EventKind.EXERCISE: "Exercised",
    EventKind.MATURITY: "Matured",
}


@dataclass
class PositionLeg:
    symbol: str
    underlying: str
    instrument_type: InstrumentType
    option: OptionDetails | None
    sign: int  # +1 long, -1 short
    opened: Decimal
    open_value: Decimal = ZERO
    open_fees: Decimal = ZERO
    closed: Decimal = ZERO
    close_value: Decimal = ZERO
    close_fees: Decimal = ZERO
    rolled: bool = False  # closed by a roll; the position carries on without it
    banked: Decimal = ZERO  # P/L realized before shares were added (#45)

    @property
    def remaining(self) -> Decimal:
        return self.opened - self.closed

    @property
    def realized(self) -> Decimal:
        """P/L of the closed part, net of fees. Opening value and fees are
        booked in proportion to the quantity closed."""
        if self.opened == 0 or self.closed == 0:
            return self.banked
        share = self.closed / self.opened
        return (
            self.banked
            + (self.open_value - self.open_fees) * share
            + self.close_value
            - self.close_fees
        )

    def bank(self) -> None:
        """Keep what's been realized and carry only the shares still held, at
        their average cost, so that adding shares later doesn't change the
        P/L of earlier sales."""
        if self.closed == 0:
            return
        self.banked = self.realized
        share = self.remaining / self.opened
        self.open_value, self.open_fees = self.open_value * share, self.open_fees * share
        self.opened, self.closed = self.remaining, ZERO
        self.close_value = self.close_fees = ZERO

    def as_leg(self) -> Leg:
        return Leg(self.symbol, self.instrument_type, self.option, self.sign * self.opened)


@dataclass(frozen=True)
class PositionEvent:
    kind: EventKind
    at: datetime
    orders: tuple[OrderGroup, ...]
    realized: Decimal  # P/L this event booked

    @property
    def value(self) -> Decimal:
        return sum((order.value for order in self.orders), ZERO)

    @property
    def fees(self) -> Decimal:
        return sum((order.fees for order in self.orders), ZERO)


@dataclass
class Position:
    key: str
    broker: str
    account_number: str
    opened_at: datetime
    legs: list[PositionLeg]
    units: Decimal  # spreads (or shares, contracts) the position holds
    entry_price: Decimal  # per unit, + credit, - debit
    events: list[PositionEvent] = field(default_factory=list)
    original_entry_price: Decimal | None = None  # before the first roll
    roll_count: int = 0
    closed_at: datetime | None = None
    opened_as_iron: bool = False  # opened as an iron condor or butterfly
    split_from: str | None = None  # key of the iron condor this half was split from

    @property
    def can_split(self) -> bool:
        return self.opened_as_iron and self.split_from is None

    @property
    def live_legs(self) -> list[PositionLeg]:
        return [leg for leg in self.legs if not leg.rolled]

    @property
    def status(self) -> Status:
        live = self.live_legs
        if all(leg.remaining == 0 for leg in live):
            return Status.CLOSED
        if any(leg.closed > 0 for leg in live):
            return Status.PARTIAL
        return Status.OPEN

    def awaiting_expiration(self, now: datetime) -> bool:
        """Every leg still held is an option past its expiration close, so all
        that's missing is the broker's expiration record (#2). Brokers can
        take a day or more to post it; until then the position stays open."""
        held = [leg for leg in self.live_legs if leg.remaining > 0]
        return bool(held) and all(
            leg.option is not None and now >= expiry_close(leg.option.expiration) for leg in held
        )

    @property
    def realized_pl(self) -> Decimal:
        return sum((leg.realized for leg in self.legs), ZERO)

    @property
    def fees(self) -> Decimal:
        return sum((leg.open_fees + leg.close_fees for leg in self.legs), ZERO)

    @property
    def underlying(self) -> str:
        underlyings = {leg.underlying for leg in self.live_legs}
        return underlyings.pop() if len(underlyings) == 1 else "Multiple"

    @property
    def structure(self) -> str:
        return describe([leg.as_leg() for leg in self.live_legs])

    @property
    def name(self) -> str:
        return f"{self.underlying} {self.structure}"

    @property
    def is_0dte(self) -> bool:
        """Opened on the day all its options expire."""
        expirations = {leg.option.expiration for leg in self.legs if leg.option is not None}
        return len(expirations) == 1 and expirations.pop() == market_date(self.opened_at)

    @property
    def one_right(self) -> str | None:
        """ "Put" or "Call" when every option leg is that type, as a MEIC half (Tracker2's
        put_call_chip); share legs don't count. None for a mix, or no options."""
        rights = {leg.option.put_call for leg in self.legs if leg.option is not None}
        return rights.pop().title() if len(rights) == 1 else None

    @property
    def close_reason(self) -> str | None:
        if self.status is not Status.CLOSED or not self.events:
            return None
        return _CLOSE_REASONS.get(self.events[-1].kind)

    @property
    def exit_price(self) -> Decimal | None:
        """Per unit, signed like the entry price, over the live legs' closes."""
        closing = [
            fill
            for event in self.events
            if event.kind is not EventKind.OPEN and event.kind is not EventKind.ROLL
            for order in event.orders
            for fill in order.fills
        ]
        closed_units = _units(leg.closed for leg in self.live_legs)
        if not closing or closed_units == 0:
            return None
        return sum((_signed_price_quantity(fill) for fill in closing), ZERO) / closed_units


@dataclass
class Book:
    positions: list[Position] = field(default_factory=list)
    unmatched: list[OrderGroup] = field(default_factory=list)
    failed: list[OrderGroup] = field(default_factory=list)  # set aside after an error
    split: frozenset[str] = frozenset()  # keys of iron condors to split
    # Closing order id -> the key of the trade that sent it (a bot run's exits): that
    # trade, or its split halves, is matched first (#180)
    owners: dict[str, str] = field(default_factory=dict)
    # Positions not yet closed, per (broker, account), oldest first: matching
    # only ever looks at these, never at the whole history.
    _open: dict[tuple[str, str], list[Position]] = field(default_factory=dict)

    def add(self, positions: Iterable[Position]) -> None:
        for position in positions:
            self.positions.append(position)
            self._open.setdefault((position.broker, position.account_number), []).append(position)

    def open_in(self, broker: str, account_number: str) -> list[Position]:
        """The account's positions that aren't closed, oldest first. Closed
        ones are dropped from the index as they're found."""
        key = (broker, account_number)
        still_open = [p for p in self._open.get(key, ()) if p.status is not Status.CLOSED]
        self._open[key] = still_open
        return still_open


def build_positions(
    orders: Iterable[OrderGroup], split: Iterable[str] = (), owners: dict[str, str] | None = None
) -> Book:
    """Every position the orders make, oldest first, plus the closing orders
    that matched none. `split` holds the keys of iron condors to track as a
    put spread and a call spread. `owners`: closing order id -> the trade that sent
    it, matched before the oldest (a bot's close of its own trade, when another
    trade holds the same strikes)."""
    book = Book(split=frozenset(split), owners=dict(owners or {}))
    for batch in _batches(orders):
        try:
            _apply(book, batch)
        except Exception:  # noqa: BLE001 -- one bad record must not hide the rest
            logger.exception("can't place orders %s", [o.order_id for o in batch])
            book.failed.extend(batch)
    return book


# --- Processing ---------------------------------------------------------------


def _batches(orders: Iterable[OrderGroup]) -> list[list[OrderGroup]]:
    """Orders that belong together (the close and open halves of one broker
    order), in the order they happened."""
    batches: dict[tuple, list[OrderGroup]] = {}
    for order in orders:
        if order.kind is Kind.TRADE and order.order_id is not None:
            key = (order.broker, order.account_number, order.order_id)
        else:
            key = (id(order),)
        batches.setdefault(key, []).append(order)

    def order_key(batch):
        # At the same moment, opens before closes: a broker correction posts
        # both at once, and the close needs the open to exist.
        opens = any(o.effect is Effect.OPEN for o in batch)
        return (min(o.executed_at for o in batch), 0 if opens else 1)

    return sorted(batches.values(), key=order_key)


def _apply(book: Book, batch: list[OrderGroup]) -> None:
    closes, opens = [], []
    for order in batch:
        if _is_closing(book, order):
            closes.append(_as_closing(order))
        else:
            opens.append(order)

    if len(closes) == 1 and opens and closes[0].kind is Kind.TRADE:
        position = _match(book, closes[0])
        if position is not None and _is_roll(position, closes[0], opens):
            _roll(position, closes[0], opens)
            return

    for order in closes:
        if order.kind is Kind.OTHER:  # a broker event we don't know how to apply
            book.unmatched.append(order)
            continue
        if order.kind is not Kind.TRADE:
            _settle(book, order)
            continue
        position = _match(book, order)
        if position is not None:
            _close(position, order, _CLOSE_EVENTS[order.kind])
        elif not _close_split_halves(book, order) and not _settle(book, order, completely=True):
            book.unmatched.append(order)
    for order in opens:
        if not _add_to_running_stock(book, order):
            book.add(_open(book, order))


def _settle(book: Book, order: OrderGroup, *, completely: bool = False) -> bool:
    """Apply a closing order leg by leg: each fill's quantity closes the
    oldest open positions holding that contract, split across several if need
    be. Whatever no position holds is unmatched; with `completely`, nothing
    is applied unless everything fits. Returns whether anything was applied."""
    per_position: dict[str, tuple[Position, list[Fill]]] = {}
    leftover: list[Fill] = []
    for fill in _with_direction(order):
        if fill.side is None:
            leftover.append(fill)
            continue
        sign = 1 if fill.side is Side.SELL else -1  # selling closes a long
        to_settle = fill.quantity
        for position in _open_positions(book, order):
            for held in position.live_legs:
                if to_settle == 0:
                    break
                if held.symbol != fill.symbol or held.sign != sign or held.remaining <= 0:
                    continue
                already = sum(
                    (
                        f.quantity
                        for f in per_position.get(position.key, (None, []))[1]
                        if f.symbol == fill.symbol
                    ),
                    ZERO,
                )
                take = min(to_settle, held.remaining - already)
                if take <= 0:
                    continue
                per_position.setdefault(position.key, (position, []))[1].append(
                    _portion(fill, take)
                )
                to_settle -= take
        if to_settle > 0:
            leftover.append(_portion(fill, to_settle))

    if completely and leftover or not per_position:
        if not completely and leftover:
            book.unmatched.append(replace(order, fills=tuple(leftover), legs=legs_of(leftover)))
        return False
    for position, fills in per_position.values():
        part = replace(order, fills=tuple(fills), legs=legs_of(fills))
        _close(position, part, _CLOSE_EVENTS[order.kind])
    if leftover:
        book.unmatched.append(replace(order, fills=tuple(leftover), legs=legs_of(leftover)))
    return True


def _with_direction(order: OrderGroup) -> list[Fill]:
    """The order's fills, each with a direction. A cash-settled assignment or
    exercise comes as two rows without one, for each contract: the removal of
    the option, and the cash settlement. They're combined into one fill: the
    option's quantity once, the cash once. The direction follows from the
    event: only a short option is assigned (bought back), only a long one is
    exercised (sold)."""
    implied = {Kind.ASSIGNMENT: Side.BUY, Kind.EXERCISE: Side.SELL}.get(order.kind)
    fills, undirected = [], {}
    for fill in order.fills:
        if fill.side is not None or implied is None:
            fills.append(fill)
        else:
            undirected.setdefault(fill.symbol, []).append(fill)
    for rows in undirected.values():
        fills.append(
            replace(
                rows[0],
                side=implied,
                quantity=max(row.quantity for row in rows),
                value=sum((row.value for row in rows), ZERO),
                fees=sum((row.fees for row in rows), ZERO),
            )
        )
    return fills


def _portion(fill: Fill, quantity: Decimal) -> Fill:
    """`quantity` of a fill, with its cash and fees in proportion."""
    if quantity == fill.quantity:
        return fill
    share = quantity / fill.quantity
    return replace(fill, quantity=quantity, value=fill.value * share, fees=fill.fees * share)


def _open_positions(book: Book, order: OrderGroup) -> list[Position]:
    """Positions in the order's account still open when it happened, oldest first; the
    trade that sent the order (book.owners) comes first."""
    owner = book.owners.get(order.order_id) if order.order_id else None
    return sorted(
        (
            p
            for p in book.open_in(order.broker, order.account_number)
            if p.opened_at <= order.executed_at
        ),
        key=lambda p: (owner is None or owner not in (p.key, p.split_from), p.opened_at),
    )


def _is_closing(book: Book, order: OrderGroup) -> bool:
    if order.effect is Effect.OPEN:
        return False
    if order.effect is Effect.CLOSE or order.kind is not Kind.TRADE:
        return True
    # The broker didn't say open or close: it closes if it can.
    return _match(book, _as_closing(order)) is not None


def _as_closing(order: OrderGroup) -> OrderGroup:
    """An order the broker didn't label is grouped as if it opens (a buy is
    +long). Seen as a close, its legs are the position it closes: flipped."""
    if order.effect is not None or order.kind is not Kind.TRADE:
        return order
    flipped = tuple(replace(leg, quantity=-leg.quantity) for leg in order.legs)
    return replace(order, legs=flipped)


def _match(book: Book, order: OrderGroup) -> Position | None:
    """The oldest still-open position in the order's account that every fill
    of the closing order fits."""
    for position in _open_positions(book, order):
        if _allocate(position, order) is not None:
            return position
    return None


def _allocate(position: Position, order: OrderGroup) -> list[tuple[Fill, PositionLeg]] | None:
    """Which live leg each fill of a closing order closes, or None if any
    fill doesn't fit: an instrument the position doesn't hold, the wrong
    direction, or more than is left."""
    left = {id(leg): leg.remaining for leg in position.live_legs}
    plan = []
    for fill in order.fills:
        if fill.side is None:
            return None
        sign = 1 if fill.side is Side.SELL else -1  # selling closes a long
        held = next(
            (
                leg
                for leg in position.live_legs
                if leg.symbol == fill.symbol and leg.sign == sign and left[id(leg)] >= fill.quantity
            ),
            None,
        )
        if held is None:
            return None
        left[id(held)] -= fill.quantity
        plan.append((fill, held))
    return plan or None


def _holding(position: Position, leg: Leg) -> PositionLeg | None:
    """The position's live leg that `leg` closes (same instrument and
    direction, enough left), if any."""
    sign = 1 if leg.quantity > 0 else -1
    return next(
        (
            held
            for held in position.live_legs
            if held.symbol == leg.symbol
            and held.sign == sign
            and held.remaining >= abs(leg.quantity)
        ),
        None,
    )


def _open(book: Book, order: OrderGroup) -> list[Position]:
    key = f"{order.broker}:{order.account_number}:{order.order_id or order.fills[0].transaction_id}"
    is_iron = splits_by_type(order.legs)  # #166: any quantities
    if not (is_iron and key in book.split):
        return [_open_one(order, key, opened_as_iron=is_iron)]
    halves = []
    for put_call in ("PUT", "CALL"):
        symbols = {leg.symbol for leg in order.legs if leg.option.put_call == put_call}
        half = _open_one(_part(order, symbols), f"{key}#{put_call.lower()}", opened_as_iron=False)
        half.split_from = key
        halves.append(half)
    return halves


def _add_to_running_stock(book: Book, order: OrderGroup) -> bool:
    """Shares bought (or shorted) where the account already holds that stock
    the same way join that position: one running position per stock per
    account (#45). Shares within multi-leg orders (a buy-write) don't."""
    if len(order.legs) != 1 or order.legs[0].instrument_type is not InstrumentType.STOCK:
        return False
    new = order.legs[0]
    sign = 1 if new.quantity > 0 else -1
    for position in book.open_in(order.broker, order.account_number):
        held = [leg for leg in position.live_legs if leg.remaining > 0]
        if len(held) == 1 and held[0].symbol == new.symbol and held[0].sign == sign:
            leg = held[0]
            units_before = leg.remaining
            leg.bank()
            added = abs(new.quantity)
            for fill in order.fills:
                leg.open_value += fill.value
                leg.open_fees += fill.fees
            leg.opened += added
            position.entry_price = (
                position.entry_price * units_before + _price_per_unit(order.fills, added) * added
            ) / (units_before + added)
            position.units = leg.opened
            position.events.append(PositionEvent(EventKind.ADD, order.executed_at, (order,), ZERO))
            return True
    return False


def _open_one(order: OrderGroup, key: str, *, opened_as_iron: bool) -> Position:
    legs = _new_legs(order)
    units = _units(leg.opened for leg in legs)
    position = Position(
        key=key,
        broker=order.broker,
        account_number=order.account_number,
        opened_at=order.executed_at,
        legs=legs,
        units=units,
        entry_price=_price_per_unit(order.fills, units),
        opened_as_iron=opened_as_iron,
    )
    position.events.append(PositionEvent(EventKind.OPEN, order.executed_at, (order,), ZERO))
    return position


def _part(order: OrderGroup, symbols: set[str]) -> OrderGroup:
    """The part of an order that deals in `symbols`."""
    return replace(
        order,
        fills=tuple(f for f in order.fills if f.symbol in symbols),
        legs=tuple(leg for leg in order.legs if leg.symbol in symbols),
    )


def _close_split_halves(book: Book, order: OrderGroup) -> bool:
    """Close both halves of a split iron condor with one order, if that's what
    the order does."""
    parts = []
    for put_call in ("PUT", "CALL"):
        symbols = {
            leg.symbol for leg in order.legs if leg.option and leg.option.put_call == put_call
        }
        if not symbols:
            return False
        parts.append(_part(order, symbols))
    if sum(len(part.legs) for part in parts) != len(order.legs):
        return False
    halves = [_match(book, part) for part in parts]
    if None in halves or halves[0].split_from is None:
        return False
    if halves[0].split_from != halves[1].split_from:
        return False
    for half, part in zip(halves, parts, strict=True):
        _close(half, part, _CLOSE_EVENTS[order.kind])
    return True


def _new_legs(order: OrderGroup) -> list[PositionLeg]:
    signs = {leg.symbol: (1 if leg.quantity > 0 else -1) for leg in order.legs if leg.quantity}
    legs: dict[str, PositionLeg] = {}
    for fill in order.fills:
        if fill.symbol not in signs:
            continue
        leg = legs.setdefault(
            fill.symbol,
            PositionLeg(
                symbol=fill.symbol,
                underlying=fill.underlying,
                instrument_type=fill.instrument_type,
                option=fill.option,
                sign=signs[fill.symbol],
                opened=ZERO,
            ),
        )
        leg.opened += fill.quantity
        leg.open_value += fill.value
        leg.open_fees += fill.fees
    return list(legs.values())


def _close(position: Position, order: OrderGroup, kind: EventKind, *, rolled=False) -> None:
    plan = _allocate(position, order)
    if plan is None:
        raise ValueError(f"order {order.order_id} doesn't fit position {position.key}")
    before = position.realized_pl
    for fill, held in plan:
        held.closed += fill.quantity
        held.close_value += fill.value
        held.close_fees += fill.fees
        if rolled and held.remaining == 0:
            held.rolled = True
    position.events.append(
        PositionEvent(kind, order.executed_at, (order,), position.realized_pl - before)
    )
    if position.status is Status.CLOSED and position.closed_at is None:
        position.closed_at = order.executed_at


def _is_roll(position: Position, close: OrderGroup, opens: list[OrderGroup]) -> bool:
    """Tracker2's implicit roll: whole legs closed, and the order's quantities
    divide evenly into the position's size."""
    if position.is_0dte:
        return False
    closing = [leg for leg in close.legs if leg.quantity != 0]
    for leg in closing:
        held = _holding(position, leg)
        if held is None or held.remaining != abs(leg.quantity):
            return False
    quantities = [abs(leg.quantity) for leg in closing] + [
        abs(leg.quantity) for order in opens for leg in order.legs if leg.quantity != 0
    ]
    if any(q != q.to_integral_value() for q in quantities):
        return False
    size = _units(leg.remaining for leg in position.live_legs if leg.remaining > 0)
    return gcd(*(int(q) for q in quantities)) == size


def _roll(position: Position, close: OrderGroup, opens: list[OrderGroup]) -> None:
    size = _units(leg.remaining for leg in position.live_legs if leg.remaining > 0)
    before = position.realized_pl
    _close(position, close, EventKind.ROLL, rolled=True)
    position.events.pop()  # one roll event for the whole order, below
    position.closed_at = None  # all its legs rolled away for a moment; it carries on
    for order in opens:
        position.legs.extend(_new_legs(order))
    if position.original_entry_price is None:
        position.original_entry_price = position.entry_price
    fills = [fill for order in (close, *opens) for fill in order.fills]
    position.entry_price += _price_per_unit(fills, size)
    position.units = size
    position.roll_count += 1
    at = min(order.executed_at for order in (close, *opens))
    position.events.append(
        PositionEvent(EventKind.ROLL, at, (close, *opens), position.realized_pl - before)
    )


def _units(quantities: Iterable[Decimal]) -> Decimal:
    """How many whole copies of the trade its legs make: 3C:2P is 1, 6C:2P is 2
    (Tracker2 4576ae8a). 0 while any leg has none; the smallest leg if one isn't whole."""
    quantities = list(quantities)
    if not quantities or min(quantities) == 0:
        return ZERO
    if any(q != q.to_integral_value() for q in quantities):
        return min(quantities)
    return Decimal(gcd(*(int(q) for q in quantities)))


def _price_per_unit(fills: Iterable[Fill], units: Decimal) -> Decimal:
    if units == 0:
        return ZERO
    return sum((_signed_price_quantity(fill) for fill in fills), ZERO) / units


def _signed_price_quantity(fill: Fill) -> Decimal:
    """Price x quantity, + for what was sold, - for what was bought. A fill
    without a price (an expiration, a cash settlement) counts its cash amount
    in price terms for equity options, and nothing otherwise."""
    if fill.price is None:
        if fill.instrument_type is InstrumentType.OPTION:
            return fill.value / 100
        return ZERO
    sign = 1 if fill.side is Side.SELL else -1
    return sign * fill.price * fill.quantity
