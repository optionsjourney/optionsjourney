"""What a journal trade did, as alerts (#178; Tracker2's transaction alerts): it opened,
rolled, closed part of itself, or closed. Read from the trade's own events, so the
alerts say what the journal says. Expirations, assignments and the like aren't alerts
(Tracker2 leaves them out). A close also says which strikes the trade held then and,
for a rolled trade, what each roll moved (#212, Tracker2's trade-closed alert). Pure."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from optionsjourney.core.market import market_date
from optionsjourney.core.positions import EventKind, Position, PositionEvent, Status

OPEN, ROLL, PARTIAL, CLOSE = "open", "roll", "partial_close", "close"
KINDS = (OPEN, ROLL, PARTIAL, CLOSE)
LABELS = {OPEN: "Opened", ROLL: "Rolled", PARTIAL: "Partly closed", CLOSE: "Closed"}


@dataclass(frozen=True)
class TradeEventFacts:
    dedupe_key: str  # the same fill never makes a second alert
    kind: str
    position_key: str
    at: datetime
    amount: Decimal  # the cash of these fills: + received, - paid
    detail: str = ""  # a close's strikes and rolls, one per line (close_detail)


def _strikes(strikes) -> str:
    """Each leg's strike in order, as "5800/5790" ("5712.5" keeps its half)."""
    return "/".join(f"{s.normalize():f}" for s in strikes)


def _roll_line(event: PositionEvent) -> str:
    """The roll as "Rolled: 2026-09-29 5800/5790 → 5750/5740": its day (New York), the
    strikes it closed, then the ones it opened. A roll's first order is its close."""
    close, *opens = event.orders
    old = _strikes(leg.option.strike for leg in close.legs if leg.option)
    new = _strikes(leg.option.strike for order in opens for leg in order.legs if leg.option)
    arrow = f" → {new}" if new else ""
    return f"Rolled: {market_date(event.at).isoformat()} {old}{arrow}"


def close_detail(position: Position) -> str:
    """What the closed-trade alert adds (#212): "Strikes: …" of the legs held at the
    close (rolled-away legs aren't), then a "Rolled: …" line per roll. Empty for a
    trade with no options."""
    held = _strikes(leg.option.strike for leg in position.live_legs if leg.option)
    lines = [f"Strikes: {held}"] if held else []
    lines += [_roll_line(e) for e in position.events if e.kind is EventKind.ROLL]
    return "\n".join(lines)


def events_of(position: Position) -> list[TradeEventFacts]:
    """The trade's alerts, oldest first. Its last close is "close" once nothing is left
    open; each close before it is "partial_close" (a stop on one side, then the wing)."""
    trades = [e for e in position.events if e.kind in (EventKind.OPEN, EventKind.CLOSE,
                                                       EventKind.ROLL)]  # fmt: skip
    last_close = max(
        (i for i, e in enumerate(trades) if e.kind is EventKind.CLOSE), default=None
    )
    facts = []
    for i, event in enumerate(trades):
        if event.kind is EventKind.OPEN:
            kind = OPEN
        elif event.kind is EventKind.ROLL:
            kind = ROLL
        elif i == last_close and position.status is Status.CLOSED:
            kind = CLOSE
        else:
            kind = PARTIAL
        orders = ",".join(sorted(str(o.order_id or o.executed_at.isoformat()) for o in event.orders))
        facts.append(TradeEventFacts(f"{kind}:{position.key}:{orders}", kind, position.key,
                                     event.at, event.value,
                                     close_detail(position) if kind == CLOSE else ""))  # fmt: skip
    return facts
