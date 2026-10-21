"""Estimating an expired trade's P/L before the broker posts its expiration
record (#23, following #2): each remaining option settles at its intrinsic
value at the underlying's close. The broker's record replaces the estimate
when it arrives; estimates are never counted in totals.

No estimate (None) when it can't be trusted:
- monthly SPX, NDX and RUT options, which settle on the next morning's
  opening prices rather than the close;
- a stock option that finished in the money, which becomes shares;
- futures options, whose multipliers vary;
- a close the brokers don't have.

Settling at the close (#147, decision 0062) uses the same rules, plus
Tracker2's: a contract with an unmatched close in the account is left alone.
A settlement becomes a made-up expiration fill (its transaction id starts
with SETTLED_PREFIX) that goes through the normal position building; a
broker's own expiration, assignment or exercise of the contract drops it."""

from calendar import FRIDAY
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from optionsjourney.core.accounts import INDEXES
from optionsjourney.core.expiry_prices import PriceRead
from optionsjourney.core.market import expiry_close
from optionsjourney.core.positions import Position, PositionLeg, Status
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
)

ZERO = Decimal("0")
MULTIPLIER = Decimal("100")
AM_SETTLED_ROOTS = {"SPX", "NDX", "RUT", "DJX"}  # their weeklies (SPXW, NDXP, RUTW) settle PM
SETTLED_PREFIX = "app-settled:"
EXERCISE_FEE = Decimal("5")  # TastyTrade's flat fee on a trade with an option in the money
BROKER_RECORDS = {Kind.EXPIRATION, Kind.ASSIGNMENT, Kind.EXERCISE}


def is_third_friday(day: date) -> bool:
    return day.weekday() == FRIDAY and 15 <= day.day <= 21


def estimate_expiration(
    position: Position, closes: Mapping[tuple[str, date], Decimal]
) -> Decimal | None:
    """The trade's total P/L if what's left of it settles at the closes, keyed
    by (underlying, day); None if it can't be estimated."""
    estimate = position.realized_pl
    for leg in position.live_legs:
        if leg.remaining <= 0:
            continue
        if _skip_reason(leg) is not None:
            return None
        close = closes.get((leg.underlying, leg.option.expiration))
        if close is None:
            return None
        intrinsic = intrinsic_value(leg.option, close)
        if intrinsic > 0 and leg.underlying not in INDEXES:
            return None  # assigned or exercised into shares, not settled in cash
        share = leg.remaining / leg.opened
        estimate += (leg.open_value - leg.open_fees) * share
        estimate += leg.sign * intrinsic * leg.remaining * MULTIPLIER
    return estimate


def intrinsic_value(option: OptionDetails, close: Decimal) -> Decimal:
    """Per share: a put's strike less the close, a call's close less the strike, or 0."""
    if option.put_call == "CALL":
        return max(close - option.strike, ZERO)
    return max(option.strike - close, ZERO)


def _skip_reason(leg: PositionLeg) -> str | None:
    """Why a leg can't be settled from a close, before looking at the price."""
    if leg.instrument_type is InstrumentType.FUTURE_OPTION:
        return "futures option; it becomes a futures contract"
    if leg.instrument_type is not InstrumentType.OPTION or leg.option is None:
        return "holds something other than options"
    root = leg.symbol[:6].strip()
    if root in AM_SETTLED_ROOTS and is_third_friday(leg.option.expiration):
        return f"{root} monthly option; it settles on the next morning's open"
    return None


# --- Settling at the close (#147) ----------------------------------------------------


@dataclass(frozen=True)
class Settlement:
    """What's left of one contract in one account, settled at the close."""

    broker: str
    account_number: str
    symbol: str
    underlying: str
    option: OptionDetails
    sign: int  # +1 long, -1 short
    quantity: Decimal
    close: Decimal  # the underlying's close used
    fee: Decimal = ZERO
    status: str = "final"  # "prelim" until the official close replaces the quick price
    source: str = ""  # where the price came from

    @property
    def value(self) -> Decimal:
        """Cash: a long in the money receives its intrinsic value, a short pays it."""
        return self.sign * intrinsic_value(self.option, self.close) * self.quantity * MULTIPLIER


@dataclass(frozen=True)
class Skipped:
    position_key: str
    name: str
    reason: str


def settle_expiring(
    positions: Iterable[Position],
    day: date,
    prices: Mapping[tuple[str, date], PriceRead],
    unmatched: set[tuple[str, str, str]] = frozenset(),
) -> tuple[list[Settlement], list[Skipped]]:
    """Settle the option legs of open trades that expire on `day` at the
    prices, keyed by (underlying, day). `unmatched` holds (broker, account,
    symbol) of contracts with an unmatched close. A trade is settled whole
    (every leg expiring that day) or skipped with a reason; a contract shared
    with a skipped trade is skipped too, since a settlement closes a contract
    oldest trade first."""
    candidates: list[tuple[Position, list[PositionLeg]]] = []
    for position in positions:
        if position.status is Status.CLOSED:
            continue
        legs = [
            leg
            for leg in position.live_legs
            if leg.remaining > 0 and leg.option is not None and leg.option.expiration == day
        ]
        if legs:
            candidates.append((position, legs))

    settled: dict[str, list[Settlement]] = {}
    skipped: list[Skipped] = []
    for position, legs in candidates:
        reason, rows = _settle_position(position, legs, day, prices, unmatched)
        if reason is None:
            settled[position.key] = rows
        else:
            skipped.append(Skipped(position.key, position.name, reason))

    def contracts(p):
        return {
            (p.broker, p.account_number, leg.symbol) for leg in p.live_legs if leg.remaining > 0
        }

    blocked = set().union(*(contracts(p) for p, _ in candidates if p.key not in settled))
    changed = True
    while changed:  # a skip can block another trade's contract, and so on
        changed = False
        for position, _ in candidates:
            if position.key in settled and contracts(position) & blocked:
                del settled[position.key]
                skipped.append(
                    Skipped(position.key, position.name, "shares a contract with a skipped trade")
                )
                blocked |= contracts(position)
                changed = True

    combined: dict[tuple, Settlement] = {}
    for rows in settled.values():
        for row in rows:
            key = (row.broker, row.account_number, row.symbol, row.sign)
            if key in combined:
                old = combined[key]
                row = Settlement(
                    **{
                        **old.__dict__,
                        "quantity": old.quantity + row.quantity,
                        "fee": old.fee + row.fee,
                    }
                )
            combined[key] = row
    return list(combined.values()), skipped


def _settle_position(position, legs, day, prices, unmatched):
    rows = []
    for leg in legs:
        reason = _skip_reason(leg)
        if reason is not None:
            return reason, []
        if (position.broker, position.account_number, leg.symbol) in unmatched:
            return "an unmatched close on one of its contracts; match it first", []
        read = prices.get((leg.underlying, day))
        if read is None:
            return f"no close for {leg.underlying} yet", []
        close = read.price
        if intrinsic_value(leg.option, close) > 0 and leg.underlying not in INDEXES:
            return (
                f"{leg.underlying} closed in the money at {close}; it becomes shares, "
                "so the broker's record is needed",
                [],
            )
        rows.append(
            Settlement(
                position.broker,
                position.account_number,
                leg.symbol,
                leg.underlying,
                leg.option,
                leg.sign,
                leg.remaining,
                close,
                status="final" if read.final else "prelim",
                source=read.source,
            )
        )
    if position.broker == "tastytrade":
        itm = next((i for i, r in enumerate(rows) if r.value != 0), None)
        if itm is not None:
            rows[itm] = Settlement(**{**rows[itm].__dict__, "fee": EXERCISE_FEE})
    return None, rows


def settlement_fill(settlement: Settlement) -> Fill:
    """A settlement as an expiration fill, closing the leg like a broker's record."""
    expiration = settlement.option.expiration
    return Fill(
        broker=settlement.broker,
        account_number=settlement.account_number,
        transaction_id=f"{SETTLED_PREFIX}{expiration.isoformat()}:{settlement.symbol}:{settlement.sign}",
        leg=0,
        executed_at=expiry_close(expiration),
        kind=Kind.EXPIRATION,
        instrument_type=InstrumentType.OPTION,
        symbol=settlement.symbol,
        underlying=settlement.underlying,
        option=settlement.option,
        side=Side.SELL if settlement.sign > 0 else Side.BUY,
        effect=Effect.CLOSE,
        quantity=settlement.quantity,
        price=None,
        value=settlement.value,
        fees=settlement.fee,
        description=f"Settled by the app at {settlement.underlying}'s "
        + (
            f"4:15 PM price of {settlement.close} (prelim)"
            if settlement.status == "prelim"
            else f"close of {settlement.close}"
        ),
    )


def with_settlements(fills: list[Fill], settlements: Iterable[Settlement]) -> list[Fill]:
    """The fills plus the settlements the brokers haven't replaced: a broker's
    own expiration, assignment or exercise of a contract always wins."""
    recorded = {
        (f.broker, f.account_number, f.symbol)
        for f in fills
        if f.kind in BROKER_RECORDS and not is_settlement(f)
    }
    extra = [
        settlement_fill(s)
        for s in settlements
        if (s.broker, s.account_number, s.symbol) not in recorded
    ]
    return [*fills, *extra] if extra else fills


def is_settlement(fill: Fill) -> bool:
    return fill.transaction_id.startswith(SETTLED_PREFIX)


def settled_by_app(position: Position) -> bool:
    """Closed (in part) by a settlement the broker hasn't confirmed yet."""
    return any(
        is_settlement(fill)
        for event in position.events
        for order in event.orders
        for fill in order.fills
    )
