"""Integrity checks over a live bot run's exits (spec: bots slice 8, section 6;
Tracker2's integrity.py). Pure: services/bot_integrity.py reads the rows, keeps
the findings and alerts. Alert only, never repair: nothing here, or in the
service, places, cancels or replaces an order or rewrites a run.

A run is checked while it's live (some leg not past its expiry). Each check
looks at one side's exit rows and says what's wrong in plain words. Slice 13: a
side is any group; a row carries its legs and its anchor (the leg a resting stop is
on; None for a group the app watches on its P&L)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from optionsjourney.bots.exit_math import after_expiry, closed_whole, split_legs, stop_past_expiry
from optionsjourney.bots.timing import side_state
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.core.symbols import parse_occ

# A stop is recorded filled, then the wing is handled: give that a minute
# (integrity.PAIRED_CLOSE_GRACE). A failed stop may be re-armed on the next
# monitor pass (every 30 s): give that two.
WING_GRACE = timedelta(seconds=60)
STOP_GRACE = timedelta(minutes=2)
# A stop that is, or is about to be, in place; "watching": watched by the app (slice 11)
PROTECTING = ("placing", "pending", "working", "watching")


@dataclass(frozen=True)
class ExitFact:
    """What a check needs of one bot_exits row."""

    id: int
    side: int  # group_position
    kind: str
    state: str
    order_id: str | None
    legs: tuple[str, ...]  # the row's leg symbols (a wing row: the one leg it sells)
    anchor: str | None  # the stop leg; None for a group the app watches
    updated_at: datetime


@dataclass(frozen=True)
class Finding:
    check: str  # wing_missing, stop_without_order, side_unprotected, exits_off
    subject: str  # what it's about, stable across passes: "side 0", "exit 12"
    summary: str


def is_live(symbols: Iterable[str], now: datetime) -> bool:
    """A run is checked while any of its legs hasn't expired."""
    return any(not after_expiry(symbol, now) for symbol in symbols)


def _sides(exits: list[ExitFact]) -> dict[int, list[ExitFact]]:
    by_side: dict[int, list[ExitFact]] = {}
    for row in exits:
        by_side.setdefault(row.side, []).append(row)
    return by_side


def _name(names: dict[int, str], side: int) -> str:
    return names.get(side) or f"Group {side + 1}"


def _expired(row: ExitFact, now: datetime) -> bool:
    """The row's stop is past expiry: its anchor's, else (a group the app watches) any
    leg's, as bot_exits.stop_expired. A split group is far_legs_held's (slice 14)."""
    return stop_past_expiry(row.anchor, row.legs, now)


# Kinds that protect a group rather than close it (integrity._PROTECTION_KINDS): a
# filled one says nothing about whether the stop's other legs were taken care of.
_PROTECTION_KINDS = ("stop", "target")
# A close by another route that's done: it sold its legs, or found nothing held.
_CLOSED = ("filled", "expired")


def _handles(row: ExitFact) -> bool:
    """A row that takes care of its legs after a stop: a wing row in any state, or a
    close by another route that's done (_closed_by_another_row: your close from the run
    page, a timed close or time action, once the wing chase died at the broker)."""
    if row.kind == "wing":
        return True
    return row.kind not in _PROTECTION_KINDS and row.state in _CLOSED


def _unhandled(stop: ExitFact, rows: list[ExitFact]) -> list[str]:
    """The legs a filled stop left that nothing took (remainder._open_legs_of_group):
    every leg but the anchor, less the legs of the group's wing rows in any state and
    of its done closes (_handles)."""
    handled = {s.strip() for r in rows if _handles(r) for s in r.legs}
    anchor = (stop.anchor or "").strip()
    return [s for s in stop.legs if s.strip() not in (anchor, *handled)]


# Each check takes the run's exit rows, its side names, now, and `off`: the sides
# whose exits you turned off (bots slice 6).
NO_SIDES: frozenset[int] = frozenset()


def wing_missing(exits, names, now, off=NO_SIDES) -> list[Finding]:
    """_check_paired_close_missing: a stop filled a minute ago and some other leg of its
    group had nothing at all done with it (no wing row for it in any state, and no
    filled close by another route covering it: _handles). A naked short has no other
    leg: nothing to miss."""
    found = []
    for side, rows in sorted(_sides(exits).items()):
        stops = [r for r in rows if r.kind == "stop" and r.state == "filled"]
        if not stops:
            continue
        stop = max(stops, key=lambda r: (r.updated_at, r.id))
        left = _unhandled(stop, rows)
        if not left or now - stop.updated_at < WING_GRACE:
            continue
        if all(after_expiry(symbol, now) for symbol in left):
            continue
        others = [s for s in stop.legs if s.strip() != (stop.anchor or "").strip()]
        what = (
            "its wing"
            if len(others) == 1
            else "the group's other legs " + ", ".join(s.strip() for s in left)
        )
        found.append(Finding("wing_missing", f"side {side}", (
            f"{_name(names, side)}'s stop filled, but nothing was done with {what}. "
            "It may still be open: check your broker."
        )))  # fmt: skip
    return found


def stop_without_order(exits, names, now, off=NO_SIDES) -> list[Finding]:
    """A stop shown as working with no order number: the monitor can't read it,
    so its fill would never be seen (_check_unattributed_stop's kind of row)."""
    return [
        Finding(
            "stop_without_order",
            f"exit {row.id}",
            (
                f"{_name(names, row.side)}'s stop shows as working but has no order number, "
                "so the app can't watch it. Check your broker."
            ),
        )  # fmt: skip
        for row in exits
        if row.kind == "stop" and row.state == "working" and not row.order_id
    ]


def side_unprotected(exits, names, now, off=NO_SIDES) -> list[Finding]:
    """An open side that has had a stop, with none in place or on its way for two
    minutes: the lasting form of "That side has no stop". In place: a resting order, or
    a live watch for a group the app watches ("watching"). A group a part-done leg-out
    left to you (its stop cancelled, never put back) is one of these, said once. A side
    whose exits you turned off is exits_off's, not this."""
    found = []
    for side, rows in sorted(_sides(exits).items()):
        if side in off:
            continue
        stops = [r for r in rows if r.kind == "stop"]
        if not stops or side_state((r.kind, r.state) for r in rows) != "open":
            continue
        if any(r.state in PROTECTING for r in stops):
            continue
        latest = max(stops, key=lambda r: (r.updated_at, r.id))
        if now - latest.updated_at < STOP_GRACE or _expired(latest, now):
            continue
        found.append(Finding("side_unprotected", f"side {side}", (
            f"{_name(names, side)} has no working stop. Check your broker."
        )))  # fmt: skip
    return found


def exits_off(exits, names, now, off=NO_SIDES) -> list[Finding]:
    """A side still open whose stop you switched off: nothing protects it from a loss,
    so it's said once and shown until you switch it back on or the side closes (bots
    slice 6; the warning Tracker2's drawer gives as "SL off"). `off`: the sides, or
    {side: whether the target is off too} (bots slice 18: both off is slice 6's "exits
    off")."""
    sides = _sides(exits)
    found = []
    for side in sorted(off):
        rows = sides.get(side, [])
        if side_state((r.kind, r.state) for r in rows) == "closed":
            continue
        if rows and all(_expired(r, now) for r in rows):
            continue
        both = off.get(side, True) if hasattr(off, "get") else True
        if both:
            summary = (
                f"{_name(names, side)}: you turned its exits off, so it has no stop or target "
                "(its timed close and rules still run). Turn them back on from the run page."
            )
        else:
            summary = (
                f"{_name(names, side)}: you switched its stop off, so it has no stop. Switch "
                "it back on from the run page."
            )
        found.append(Finding("exits_off", f"side {side}", summary))
    return found


# Slice 14: the settle row (services/bot_settle.py) records far legs left after the
# nearer legs expired.
GONE = ("filled", "expired")  # a wing row that sold its leg or found it gone
_GROUP_KINDS = ("stop", "target", "timed", "close", "action", "watch", "profit")


def _expiry_close(symbols: Iterable[str]) -> datetime | None:
    """When the latest of these options expired: its expiration day's session close."""
    found = None
    for symbol in symbols:
        parsed = parse_occ(symbol)
        if parsed is None:
            continue
        day = parsed[1].expiration
        close = session_close(day) or time(16, 0)
        moment = datetime.combine(day, close, tzinfo=MARKET_TZ)
        found = moment if found is None or moment > found else found
    return found


def far_legs_held(exits, names, now, off=NO_SIDES) -> list[Finding]:
    """A group whose nearer legs expired with far legs still held that no wing row sold
    (slice 14). A timed close should have closed it first (validation), so this should
    never happen; the bot leaves the far legs to you. Found once its settle row is
    written, or two minutes after the expiry without one."""
    found = []
    for side, rows in sorted(_sides(exits).items()):
        if closed_whole((r.kind, r.state) for r in rows):
            continue  # a close took the whole group before its nearer legs expired
        group = [r for r in rows if r.kind in _GROUP_KINDS]
        settles = [r for r in rows if r.kind == "settle"]
        legs = tuple(dict.fromkeys(s for r in group + settles for s in r.legs))
        expired, live = split_legs(legs, now)
        if not expired or not live:
            continue
        sold = {s.strip() for r in rows if r.kind == "wing" and r.state in GONE for s in r.legs}
        held = [s for s in live if s.strip() not in sold]
        if not held:
            continue
        if not settles:
            since = _expiry_close(expired)
            if since is None or now - since < STOP_GRACE:
                continue
        found.append(Finding("far_legs_held", f"side {side}", (
            f"{_name(names, side)}'s nearer legs expired; {', '.join(s.strip() for s in held)} "
            f"{'is' if len(held) == 1 else 'are'} still held and nothing protects "
            f"{'it' if len(held) == 1 else 'them'}: yours to sell or keep. Check your broker."
        )))  # fmt: skip
    return found


CHECKS = (wing_missing, stop_without_order, side_unprotected, exits_off, far_legs_held)


def findings(
    exits: Iterable[ExitFact], names: dict[int, str], now: datetime, off=NO_SIDES
) -> list[Finding]:
    """Every check over one run's exit rows (the service runs each on its own)."""
    rows = list(exits)
    return [f for check in CHECKS for f in check(rows, names, now, off)]
