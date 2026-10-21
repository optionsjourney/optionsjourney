"""What a bot trade's group should have resting, and what's missing (spec: bots slice
18, section 1; Tracker2's services/bots/rearm.py, `_wanted_group` and `_present`).
Pure: services/bot_rearm.py reads the rows, places what's missing and says so.

A group should have a stop when its bot sets one (never on a futures run, decision
0043) and a target when its bot sets one, unless you switched that one off. It's
present when a row of that kind is in force (rearm.PRESENT_STATUSES): a stop working at
the broker, pending (waiting to be placed) or watched by the app; a target armed,
firing, or for a futures run working or pending at the broker. Nothing present is ever
touched."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from optionsjourney.bots.timing import side_state

STOP_PRESENT = ("working", "pending", "watching")
TARGET_PRESENT = ("armed", "firing", "working", "pending")

# Item statuses: what the dry run says, and what Restore did
MISSING = "missing"  # not there; Restore places it
PLACED = "placed"
REFUSED = "refused"  # not placed, with why (nothing was sent, or the broker refused it)
ERROR = "error"  # something went wrong part-way
HELD = "held back"  # an earlier close or stop move may still be live: its recovery's
BUSY = "busy"  # a close is running, or its exits are being placed
BY_HAND = "set it by hand"  # missing, but there's no level to put it back at

SET_BY_HAND = "there's no level to put it back at; set it from the run page"
HELD_WORDS = (
    "a close or stop move on this group may still be working at your broker; check your "
    "broker (the run page has a re-check)"
)
BUSY_WORDS = "a close is running on it, or its exits are being placed; check again soon"


@dataclass(frozen=True)
class Row:
    """What the plan needs of one bot_exits row."""

    id: int
    kind: str
    state: str
    trigger: Decimal | None = None
    limit: Decimal | None = None


@dataclass(frozen=True)
class Want:
    """One group's settings: what its bot sets, and what you switched off."""

    stop: bool  # the bot's group sets a stop (and the run isn't futures)
    target: bool  # it sets a target
    stop_off: bool = False
    target_off: bool = False
    held_back: bool = False  # bot_exits.held_back said so
    target_price: Decimal | None = None  # a target's close-at when no row has one


@dataclass(frozen=True)
class Missing:
    kind: str  # "stop", "target", or "group" (a whole group: held back or busy)
    status: str  # MISSING, BY_HAND, HELD or BUSY
    row_id: int | None  # the row Restore re-uses (the latest of its kind), if any
    price: Decimal | None  # a stop's trigger, a target's close-at
    detail: str = ""


def _latest(rows: list[Row], kind: str) -> Row | None:
    found = [r for r in rows if r.kind == kind]
    return max(found, key=lambda r: r.id) if found else None


def missing(rows: Iterable[Row], want: Want) -> list[Missing]:
    """What's missing on one group, stop first (Tracker2 places the broker-resting side
    first). A closed group has nothing missing; a busy or held-back one that lacks
    something is said as such and nothing of it is placed (its own job or recovery is at
    work)."""
    rows = list(rows)
    state = side_state((r.kind, r.state) for r in rows)
    if state == "closed":
        return []
    out = []
    if (
        want.stop
        and not want.stop_off
        and not any(r.kind == "stop" and r.state in STOP_PRESENT for r in rows)
    ):
        latest = _latest(rows, "stop")
        if latest is None or latest.state != "expired":
            price = latest.trigger if latest is not None else None
            if latest is None or price is None:
                out.append(Missing("stop", BY_HAND, latest and latest.id, price, SET_BY_HAND))
            else:
                out.append(Missing("stop", MISSING, latest.id, price))
    if (
        want.target
        and not want.target_off
        and not any(r.kind == "target" and r.state in TARGET_PRESENT for r in rows)
    ):
        latest = _latest(rows, "target")
        if latest is None or latest.state != "expired":
            price = latest.limit if latest is not None and latest.limit is not None else None
            price = price if price is not None else want.target_price
            if price is None:
                out.append(Missing("target", BY_HAND, latest and latest.id, None, SET_BY_HAND))
            else:
                out.append(Missing("target", MISSING, latest and latest.id, price))
    if out and state == "busy":
        return [Missing("group", BUSY, None, None, BUSY_WORDS)]
    if out and want.held_back:
        return [Missing("group", HELD, None, None, HELD_WORDS)]
    return out
