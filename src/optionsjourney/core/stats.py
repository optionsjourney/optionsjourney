"""Summary numbers for a set of positions: P/L, win rate, averages."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from optionsjourney.core.market import market_date

from optionsjourney.core.positions import EventKind, Position, Status

ZERO = Decimal("0")


@dataclass(frozen=True)
class Summary:
    trades: int
    open: int
    closed: int
    wins: int
    losses: int
    realized: Decimal  # all realized P/L, including partly closed positions
    realized_closed: Decimal  # realized P/L of the closed positions only
    best: Decimal | None
    worst: Decimal | None

    @property
    def win_rate(self) -> Decimal | None:
        """Winners as a share of closed trades, 0-100."""
        return Decimal(self.wins * 100) / self.closed if self.closed else None

    @property
    def average(self) -> Decimal | None:
        """Average realized P/L per closed trade."""
        return self.realized_closed / self.closed if self.closed else None


def summarize(positions: Iterable[Position]) -> Summary:
    positions = list(positions)
    closed = [p.realized_pl for p in positions if p.status is Status.CLOSED]
    return Summary(
        trades=len(positions),
        open=sum(1 for p in positions if p.status is not Status.CLOSED),
        closed=len(closed),
        wins=sum(1 for pl in closed if pl > 0),
        losses=sum(1 for pl in closed if pl < 0),
        realized=sum((p.realized_pl for p in positions), ZERO),
        realized_closed=sum(closed, ZERO),
        best=max(closed, default=None),
        worst=min(closed, default=None),
    )


# --- The journal's summary, like Tracker2's ---------------------------------------
# Tracker2's journal (services/journal_service.py) shows closed P/L, premium
# open and closed, a capture rate, and per-lot averages. Premium here comes from
# the brokers' cash amounts (the opening order plus any rolls), like P/L does,
# so contract multipliers never have to be known.


@dataclass(frozen=True)
class JournalSummary:
    trades: int  # every trade shown, open or closed
    winners: int  # closed trades that made money
    win_rate: Decimal | None  # winners over closed trades, 0-100
    closed_pl: Decimal
    open_premium: Decimal
    closed_premium: Decimal
    avg_per_lot: Decimal | None
    avg_winner_per_lot: Decimal | None
    avg_loser_per_lot: Decimal | None
    max_winner_per_lot: Decimal | None
    max_loser_per_lot: Decimal | None

    @property
    def capture_rate(self) -> Decimal | None:
        """Closed P/L as a share of the premium those trades took in, %."""
        return self.closed_pl * 100 / self.closed_premium if self.closed_premium else None


def journal_summary(positions: Iterable[Position], day: date | None = None) -> JournalSummary:
    """With `day` (the journal's "Open + closed today"), a trade still open counts only
    what it realized that day, not partial closes from before."""
    positions = list(positions)

    def realized(p: Position) -> Decimal:
        if day is None or p.status is Status.CLOSED:
            return p.realized_pl
        return sum((e.realized for e in p.events if market_date(e.at) == day), ZERO)

    closed = [p for p in positions if p.status is Status.CLOSED]
    winners = sum(1 for p in closed if p.realized_pl > 0)

    # A still-open rolled trade isn't a finished trade: what its rolls realized
    # counts once it closes, and its premium stays open in full (as in Tracker2).
    counted = [p for p in positions if p.status is Status.CLOSED or not p.roll_count]
    open_premium = closed_premium = ZERO
    lot_results: list[tuple[Decimal, Decimal]] = []  # (realized P/L, lots closed)
    for p in positions:
        if p.status is Status.CLOSED:
            closed_premium += premium_of(p)
        elif p.status is Status.PARTIAL and not p.roll_count:
            open_part, closed_part = _premium_split(p)
            open_premium += open_part
            closed_premium += closed_part
        else:
            open_premium += premium_of(p)
    for p in counted:
        lots = p.units if p.status is Status.CLOSED else _closed_lots(p)
        if lots > 0 and (p.status is Status.CLOSED or realized(p)):
            lot_results.append((realized(p), lots))

    wins = [(pl, n) for pl, n in lot_results if pl > 0]
    losses = [(pl, n) for pl, n in lot_results if pl < 0]
    return JournalSummary(
        trades=len(positions),
        winners=winners,
        win_rate=Decimal(winners * 100) / len(closed) if closed else None,
        closed_pl=sum((realized(p) for p in counted), ZERO),
        open_premium=open_premium,
        closed_premium=closed_premium,
        avg_per_lot=_per_lot(lot_results),
        avg_winner_per_lot=_per_lot(wins),
        avg_loser_per_lot=_per_lot(losses),
        max_winner_per_lot=max((pl / n for pl, n in wins), default=None),
        max_loser_per_lot=min((pl / n for pl, n in losses), default=None),
    )


def premium_of(position: Position) -> Decimal:
    """The cash the trade took in (or paid) to open, rolls included, in
    dollars. Trades without options (shares, Treasury bills) have none."""
    if not any(leg.option is not None for leg in position.legs):
        return ZERO
    return abs(
        sum(
            (e.value for e in position.events if e.kind in (EventKind.OPEN, EventKind.ROLL)),
            ZERO,
        )
    )


def net_entry(positions) -> Decimal:
    """What the trades took in or paid to open, rolls included, together: the "% of
    entry" basis of an alert (signed before adding, so two debits add up)."""
    return abs(sum(
        (e.value for p in positions for e in p.events
         if e.kind in (EventKind.OPEN, EventKind.ROLL)), ZERO,
    ))  # fmt: skip


def _premium_split(position: Position) -> tuple[Decimal, Decimal]:
    """A partly closed trade's premium, split leg by leg into the part still
    open and the part closed, like its P/L."""
    legs = [leg for leg in position.live_legs if leg.option is not None and leg.opened]
    if not legs:
        return premium_of(position), ZERO
    closed = sum((leg.open_value * leg.closed / leg.opened for leg in legs), ZERO)
    still_open = sum((leg.open_value * leg.remaining / leg.opened for leg in legs), ZERO)
    return abs(still_open), abs(closed)


def _closed_lots(position: Position) -> Decimal:
    """Lots closed so far: the most closed on any live leg."""
    return max((leg.closed for leg in position.live_legs), default=ZERO)


def _per_lot(results: list[tuple[Decimal, Decimal]]) -> Decimal | None:
    lots = sum((n for _, n in results), ZERO)
    return sum((pl for pl, _ in results), ZERO) / lots if lots else None


# --- A bot's P/L graph (#208, Tracker2 f52ae8a0 and ca044d27) -----------------------


@dataclass(frozen=True)
class TradePoint:
    at: datetime | None  # when the trade closed; None for the $0 start
    pl: Decimal | None  # that trade's P/L; None for the start
    cumulative: Decimal  # every trade's P/L up to and including this one


def trade_curve(closes: Iterable[tuple[datetime, Decimal]]) -> list[TradePoint]:
    """Cumulative P/L trade by trade, oldest close first, from a $0 start so a single
    trade still draws a line. `closes` is (closed at, P/L) per closed trade. Empty when
    nothing has closed."""
    closes = sorted(closes, key=lambda close: close[0])
    if not closes:
        return []
    curve, cumulative = [TradePoint(None, None, ZERO)], ZERO
    for at, pl in closes:
        cumulative += pl
        curve.append(TradePoint(at, pl, cumulative))
    return curve
