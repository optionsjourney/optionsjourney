"""Paper trade P/L (decision 0061): the arithmetic of a pretend trade, kept pure.

Prices are per contract and signed like a fill: a credit is positive, a debit negative.
A trade is entered at one price for the whole trade. It is closed by one or more exits,
each the closing price of the groups it closed (or of the whole trade) times the
contracts it closed, so the exits' dollars simply add up:

    P/L = (entry price x contracts + sum of exit price x exit contracts) x multiplier"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)


def trade_pl(
    entry_price: Decimal,
    contracts: int,
    multiplier: Decimal,
    exits: Iterable[tuple[Decimal, int]],
) -> Decimal:
    """The trade's P/L in dollars. `exits`: (price, contracts) of each exit or expiry."""
    closing = sum((price * n for price, n in exits), ZERO)
    return (entry_price * contracts + closing) * multiplier


def exit_price(contracts: int, exits: Iterable[tuple[Decimal, int]]) -> Decimal | None:
    """The whole trade's closing price per contract, signed like a fill (closing a credit
    trade for 4.90 is -4.90); None with no contracts."""
    if contracts <= 0:
        return None
    return sum((price * n for price, n in exits), ZERO) / contracts


@dataclass(frozen=True)
class Totals:
    pl: Decimal
    wins: int
    losses: int
    win_rate: Decimal | None  # wins over closed trades (a break-even one counts), 0-100


def totals(results: Iterable[Decimal | None]) -> Totals:
    """Totals of trades' P/Ls; None (a trade still open) is left out."""
    closed = [pl for pl in results if pl is not None]
    wins = sum(1 for pl in closed if pl > 0)
    return Totals(
        pl=sum(closed, ZERO),
        wins=wins,
        losses=sum(1 for pl in closed if pl < 0),
        win_rate=Decimal(wins * 100) / len(closed) if closed else None,
    )


def condor_sides(legs) -> dict[str, list[dict]] | None:
    """#181: a run's legs as an iron condor's put side and call side, as the journal
    splits one (structures.splits_by_type): a sold and a bought put and a sold and a
    bought call, with no group holding both. None when they aren't one."""
    legs = list(legs or ())
    pairs = sorted((leg.get("right") or "", leg.get("side") or "") for leg in legs)
    if pairs != [("call", "buy"), ("call", "sell"), ("put", "buy"), ("put", "sell")]:
        return None
    if any(len({leg["right"] for leg in legs if leg["group"] == g}) > 1
           for g in {leg["group"] for leg in legs}):  # fmt: skip
        return None
    return {right: [leg for leg in legs if leg["right"] == right] for right in ("put", "call")}


def legs_price(legs, mids: dict[str, Decimal]) -> Decimal:
    """The entry price of some of an entry's legs: each one's mid, a sold leg's as a
    credit and a bought leg's as a debit, times its ratio."""
    return sum(
        (
            mids[leg["symbol"]] * (1 if leg["side"] == "sell" else -1) * int(leg.get("ratio") or 1)
            for leg in legs
        ),
        ZERO,
    )


def closed_by(reasons: Iterable[tuple[str, str]]) -> str | None:
    """What closed a trade, from its closing fills' (kind, reason) in order: its exits'
    reasons (a stop, a target...), each once; just the last expiry's when nothing
    exited it. A stopped side whose wing then expires was closed by the stop (#181)."""
    reasons = list(reasons)
    exits = list(dict.fromkeys(reason for kind, reason in reasons if kind == "exit"))
    if exits:
        return ", ".join(exits)
    return reasons[-1][1] if reasons else None


def marked_pl(
    entry_price: Decimal,
    contracts: int,
    multiplier: Decimal,
    exits: Iterable[tuple[Decimal, int]],
    held: Iterable[tuple[str, int, Decimal | None]],
) -> Decimal | None:
    """An open trade's P/L now (#183): its entry and closes so far, and each leg still
    held, (side, count, mark), closed at its mark (a sold leg bought back, a bought one
    sold). None while a leg held has no mark."""
    closing = sum((price * n for price, n in exits), ZERO)
    for side, count, mark in held:
        if mark is None:
            return None
        closing += mark * count * (-1 if side == "sell" else 1)
    return (entry_price * contracts + closing) * multiplier
