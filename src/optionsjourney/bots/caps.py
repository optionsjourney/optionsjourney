"""Counting a bot's open trades for its max open trades cap (Tracker2's
guards.under_position_cap). Pure: it works from the app's own run and exit
records, written as orders fill, never from the journal (which only updates on
a sync).

A run is an open trade when it's a filled (or partly filled) run with fills
recorded, its options haven't expired, and at least one of its sides is still
open. A group is closed when its target, a timed close or a close you asked for
closed it, when its stop filled and every other leg of the group was sold (or
was already gone; a naked short has none), or when its stop expired because the
position is gone. Anything else, including an exit state this doesn't know, and
a close left to you part-way through its leg-out (it ends "failed": legs are
still held), leaves the group open, so the cap errs on the side of not
entering."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import reduce
from math import gcd

from optionsjourney.bots import futures
from optionsjourney.bots.exit_math import after_expiry, is_split, row_legs, split_legs
from optionsjourney.core.symbols import parse_occ

HOLDING = ("filled", "partial")  # run statuses that can hold a position
GONE = ("filled", "expired")  # a wing row's leg sold, or found already gone


@dataclass(frozen=True)
class ExitRow:
    group: int  # the group it belongs to
    kind: str  # "stop", "target", "wing", "timed", "action" or "close"
    state: str
    legs: tuple[str, ...] = ()  # the row's leg symbols (a wing row: the one leg it sells)
    anchor: str | None = None  # the leg a resting stop is on; None for a watched group

    @classmethod
    def of(cls, row) -> ExitRow:
        """A BotExit's row, duck-typed: its legs (the old short/long pair for a row
        from before slice 13) and its anchor (none for a group the app watches)."""
        anchor = None if getattr(row, "watched", False) else (
            row.anchor_symbol or row.short_symbol or None
        )  # fmt: skip
        legs = tuple(leg.symbol for leg in row_legs(row))
        return cls(row.group_position, row.kind, row.state, legs, anchor)


@dataclass(frozen=True)
class RunRow:
    root_id: int  # coalesce(parent_run_id, id): a root and its tranches share it
    run_id: int
    status: str
    legs: tuple[dict, ...]  # the run's picked legs: each has "symbol" and "group"
    exits: tuple[ExitRow, ...]
    has_fills: bool
    closed: frozenset[int] = frozenset()  # a paper run's groups its PaperFills closed


PAPER_CLOSING = ("exit", "expiry")


def paper_held(legs, contracts: int, fills) -> dict[int, int]:
    """A paper run's open count per group (decision 0061), in the group's units as a live
    run counts them (bot_exits.group_units: contracts x the gcd of its ratios): its
    entry's, less its paper exits and expiries. `fills`: (kind, group, count) of its
    PaperFills; a group of None is the whole trade."""
    fills = list(fills)
    held = {}
    for position in sorted({leg["group"] for leg in legs or ()}):
        gone = sum(n for kind, group, n in fills
                   if kind in PAPER_CLOSING and group in (position, None))  # fmt: skip
        held[position] = max(contracts * group_gcd(legs, position) - gone, 0)
    return held


def group_gcd(legs, position: int) -> int:
    """The gcd of the group's leg ratios (exit_math.group_legs): units per contract."""
    return reduce(gcd, (int(leg.get("ratio") or 1) for leg in legs if leg["group"] == position),
                  0) or 1  # fmt: skip


def paper_closed(legs, contracts: int, fills) -> frozenset[int]:
    """The groups a paper run's exits and expiries have closed in full."""
    return frozenset(p for p, n in paper_held(legs, contracts, fills).items() if n <= 0)


def _stop_closed(stop: ExitRow, exits: list[ExitRow]) -> bool:
    """A filled stop closed its group once every other leg of it is sold or gone
    (Tracker2's remainder: one close per leg still held; none for a naked short). A
    row that can't name its legs and anchor (a fixture) needs any wing sold or gone."""
    wings = [e for e in exits if e.kind == "wing" and e.state in GONE]
    if not stop.legs or stop.anchor is None:
        return bool(wings)
    others = {leg for leg in stop.legs if leg != stop.anchor}
    return others <= {leg for wing in wings for leg in wing.legs}


def _far_legs_gone(legs: tuple[str, ...], exits: list[ExitRow], now: datetime) -> bool:
    """Bots slice 14: every leg of `legs` not yet past expiry has a wing row that sold it
    or found it gone (a stop's remainder). Far legs left to you keep the
    group open until they expire."""
    _, live = split_legs(legs, now)
    sold = {leg for e in exits if e.kind == "wing" and e.state in GONE for leg in e.legs}
    return set(live) <= sold


def _expired_stop_closed(stop: ExitRow, exits: list[ExitRow], now: datetime) -> bool:
    """An expired stop closed its group (its position gone, or its options expired),
    unless the group is split (slice 14): its nearer legs expired and the far ones are
    still held until they're sold."""
    return not is_split(stop.legs, now) or _far_legs_gone(stop.legs, exits, now)


def _side_closed(exits: list[ExitRow], now: datetime) -> bool:
    def has(kind: str, *states: str) -> bool:
        return any(e.kind == kind and e.state in states for e in exits)

    return (
        has("target", "filled")
        or has("timed", "filled", "expired")  # a timed close closed it, or found nothing held
        or has("close", "filled", "expired")  # a close you asked for closed all of it (slice 6)
        or any(e.kind == "stop" and e.state == "filled" and _stop_closed(e, exits) for e in exits)
        or any(
            e.kind == "stop" and e.state == "expired" and _expired_stop_closed(e, exits, now)
            for e in exits
        )
    )


def _expired(run: RunRow, now: datetime) -> bool:
    """Past the legs' expiration (every leg must read as expired)."""
    return bool(run.legs) and all(after_expiry(leg["symbol"], now) for leg in run.legs)


def _open_in(run: RunRow, split: bool, now: datetime) -> int:
    """The trades one run holds: its open sides if the bot splits its condors,
    else 1 while any side is open."""
    if run.status not in HOLDING or not run.has_fills or _expired(run, now):
        return 0
    groups = sorted({leg["group"] for leg in run.legs})
    if not groups:
        return 1  # filled, but its legs can't be told apart: count it as held
    open_groups = {
        g for g in groups
        if g not in run.closed and not _side_closed([e for e in run.exits if e.group == g], now)
    }  # fmt: skip
    if split:
        return sum(bool(side & open_groups) for side in _sides(run.legs))
    return 1 if open_groups else 0


def _sides(legs) -> list[set[int]]:
    """#165: a split condor's sides are its put legs and its call legs, whatever the
    grouping (a MEIC-style bot has a group per leg), so a side is open while any of its
    groups is. A group that mixes puts and calls, or a leg that can't be read, leaves
    each group its own side."""
    rights: dict[int, set[str | None]] = {}
    for leg in legs:
        rights.setdefault(leg["group"], set()).add(_right(leg["symbol"]))
    if any(len(r) != 1 or None in r for r in rights.values()):
        return [{g} for g in rights]
    sides: dict[str | None, set[int]] = {}
    for group, (right,) in rights.items():
        sides.setdefault(right, set()).add(group)
    return list(sides.values())


def _right(symbol: str) -> str | None:
    """A leg's put or call, from its symbol; None when it can't be read."""
    parsed = parse_occ(symbol)
    option = parsed[1] if parsed is not None else futures.option_of(symbol)
    return option.put_call if option is not None else None


def open_trades(runs: Iterable[RunRow], split: bool, now: datetime) -> int:
    """The bot's open trades. An entry filled in tranches counts once: each root
    counts the most any single one of its runs holds (Tracker2's rule)."""
    per_root: dict[int, int] = {}
    for run in runs:
        per_root[run.root_id] = max(per_root.get(run.root_id, 0), _open_in(run, split, now))
    return sum(per_root.values())


def open_runs(runs: Iterable[RunRow], now: datetime) -> list[int]:
    """The ids of the runs still holding an open side, in order: what stops a bot
    being archived (archive.open_trade_reasons)."""
    return sorted(run.run_id for run in runs if _open_in(run, True, now))
