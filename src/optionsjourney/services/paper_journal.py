"""The Journal's Paper trades view (decision 0061): the pretend trades of Paper bots, kept apart from
the real journal. Only runs with paper=True and an entry fill count: the old
status "preview" runs from before paper trading are never listed."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import caps
from optionsjourney.core import paper_pl
from optionsjourney.core.market import market_date
from optionsjourney.core.paper_pl import Totals
from optionsjourney.db.models import Bot, BotRun, PaperFill, Tag
from optionsjourney.services.streaming import marks as hub_marks

ENTRY = "entry"


@dataclass(frozen=True)
class PaperTrade:
    run_id: int
    bot_id: int
    bot_name: str
    opened_at: datetime
    closed_at: datetime | None
    entry_price: Decimal  # per contract: a credit is positive, a debit negative
    exit_price: Decimal | None  # per contract, signed like entry_price (a debit is negative)
    contracts: int
    multiplier: Decimal
    pl: Decimal | None  # None while the trade is open
    closed_by: str | None  # its exits' reasons ("stop", "target", ...), else "expired"
    side: str | None = None  # "put" or "call": one side of a split iron condor (#181)
    held: tuple[tuple[str, str, int], ...] = ()  # (symbol, side, count) of the legs still held
    exits: tuple[tuple[Decimal, int], ...] = ()  # (price, contracts) of its closes so far
    wing_held: bool = False  # #183: closed, its sold legs gone, a bought wing still held

    @property
    def key(self) -> str:
        """The row's key on the page, for its live P/L."""
        return f"{self.run_id}:{self.side or 'all'}"


def trades(
    session_factory,
    start: date | None = None,
    end: date | None = None,
    bot_id: int | None = None,
    split: bool = False,
    tag: str | None = None,
) -> list[PaperTrade]:
    """Paper trades opened from `start` to `end` (New York days, both included), newest
    first. A trade is closed once its paper exits and expiries have closed every group.
    `split`: show the iron condors of bots that split theirs as a put side and a call
    side, as the real journal does (#181). `tag` (#219): only the trades of bots that
    carry it (_bot_tags), ignoring case."""
    with session_factory() as session:
        query = select(BotRun, Bot.name, Bot.split_iron_condors).join(Bot, Bot.id == BotRun.bot_id)
        query = query.where(BotRun.paper.is_(True))
        if bot_id is not None:
            query = query.where(BotRun.bot_id == bot_id)
        if tag:
            word = tag.strip().lower()
            tagged = [i for i, tags in _bot_tags(session).items() if word in map(str.lower, tags)]
            query = query.where(BotRun.bot_id.in_(tagged))
        runs = list(session.execute(query.order_by(BotRun.id)))
        rows: dict[int, list[PaperFill]] = {}
        ids = [run.id for run, _, _ in runs]
        if ids:
            for row in session.scalars(
                select(PaperFill).where(PaperFill.run_id.in_(ids)).order_by(PaperFill.id)
            ):
                rows.setdefault(row.run_id, []).append(row)
        out = []
        for run, name, splits in runs:
            for trade in _trades(run, name, rows.get(run.id, []), split and splits):
                opened = market_date(trade.opened_at)
                if (start is None or opened >= start) and (end is None or opened <= end):
                    out.append(trade)
    out.sort(key=lambda t: (t.opened_at, t.run_id, t.side == "put"), reverse=True)
    return out


def _bot_tags(session) -> dict[int, list[str]]:
    """{bot id: its tags} for the bots with paper runs: the journal tag it files its
    trades under (decision 0072), then its own labels (bots slice 8)."""
    rows = session.execute(
        select(Bot.id, Tag.name, Bot.tags).join(Tag, Tag.id == Bot.tag_id)
        .where(Bot.id.in_(select(BotRun.bot_id).where(BotRun.paper.is_(True))))
    )  # fmt: skip
    return {bot_id: [name, *(labels or ())] for bot_id, name, labels in rows}


def tags(session_factory) -> list[str]:
    """#219: the tag filter's choices, the tags of the bots with paper runs, by name."""
    with session_factory() as session:
        found = {t.lower(): t for names in _bot_tags(session).values() for t in reversed(names)}
    return sorted(found.values(), key=str.lower)


def _trades(run: BotRun, name: str, fills: list[PaperFill], split: bool) -> list[PaperTrade]:
    entry = next((f for f in fills if f.kind == ENTRY), None)
    if entry is None:  # no pretend fill: a skipped or refused run, never a trade
        return []
    sides = _sides(run, entry, fills) if split else None
    if sides is None:
        return [_trade(run, name, entry, entry.price, run.legs, fills, None)]
    return [
        _trade(run, name, entry, price, legs, [f for f in fills if f.group in groups], side)
        for side, (price, legs, groups) in sides.items()
    ]


def _sides(run: BotRun, entry: PaperFill, fills: list[PaperFill]):
    """Each side of the run's iron condor: (its entry price, its legs, its groups), or
    None when the run can't be split (not a condor, an exit of the whole trade, or an
    entry without its legs' mids)."""
    sides = paper_pl.condor_sides(run.legs)
    if sides is None or any(f.group is None for f in fills if f.kind in caps.PAPER_CLOSING):
        return None
    mids = {leg.get("symbol"): leg.get("mid") for leg in entry.legs or ()}
    if any(mids.get(leg["symbol"]) is None for leg in run.legs):
        return None
    mids = {symbol: Decimal(mid) for symbol, mid in mids.items() if mid is not None}
    return {
        side: (paper_pl.legs_price(legs, mids), legs, {leg["group"] for leg in legs})
        for side, legs in sides.items()
    }


def _trade(run: BotRun, name: str, entry: PaperFill, price: Decimal, legs, fills: list[PaperFill],
           side: str | None) -> PaperTrade:  # fmt: skip
    """The trade (or one side of it, entered at `price`) its `legs` and `fills` make. It
    is closed once every group with a sold leg is (#183): a bought wing left to expire
    after its short was stopped is worth 0 until it's sold or expires."""
    contracts = run.contracts or entry.contracts
    closing = [f for f in fills if f.kind in caps.PAPER_CLOSING]
    held = caps.paper_held(legs, contracts, ((f.kind, f.group, f.contracts) for f in fills))
    sold = {leg["group"] for leg in legs if leg.get("side") == "sell"} or set(held)
    done = bool(closing) and not any(held[g] for g in sold)
    last = [f for f in closing if f.group in sold or f.group is None] or closing
    exits = [(f.price, f.contracts) for f in closing]
    held_legs = tuple(
        (leg["symbol"], leg["side"],
         held[leg["group"]] * int(leg.get("ratio") or 1) // caps.group_gcd(legs, leg["group"]))
        for leg in legs if held.get(leg["group"])
    )  # fmt: skip
    return PaperTrade(
        run_id=run.id,
        bot_id=run.bot_id,
        bot_name=name,
        opened_at=entry.at,
        closed_at=last[-1].at if done else None,
        entry_price=price,
        exit_price=paper_pl.exit_price(contracts, exits) if done else None,
        contracts=contracts,
        multiplier=run.multiplier,
        pl=paper_pl.trade_pl(price, contracts, run.multiplier, exits) if done else None,
        closed_by=paper_pl.closed_by((f.kind, f.reason) for f in closing) if done else None,
        side=side,
        held=held_legs,
        exits=tuple(exits),
        wing_held=done and bool(held_legs),
    )


def live_pl(paper_trades: list[PaperTrade], hub) -> dict[str, Decimal | None]:
    """#183: each open trade's (or side's) P/L now, by its key: its closes so far and its
    legs still held at the quote hub's marks; None while a leg has no mark."""
    trades = [t for t in paper_trades if t.closed_at is None]
    symbols = {symbol for t in trades for symbol, _, _ in t.held}
    hub.subscribe(symbols)
    marks = hub_marks(hub, symbols)  # after hours, a pulled market at its last real mark (#241)
    return {
        t.key: paper_pl.marked_pl(
            t.entry_price, t.contracts, t.multiplier, t.exits,
            [(side, count, marks.get(symbol)) for symbol, side, count in t.held],
        )
        for t in trades
    }  # fmt: skip


def totals(paper_trades: list[PaperTrade]) -> Totals:
    """P/L, wins, losses and win rate of the closed trades among `paper_trades`."""
    return paper_pl.totals(t.pl for t in paper_trades)


@dataclass(frozen=True)
class LegFill:
    """One leg of a pretend fill (#182), as the run page lists them."""

    at: datetime
    what: str  # "entry", or the reason that closed it: "stop", "target", "expired", ...
    symbol: str
    leg: str  # "7700 put", or the symbol when the run's legs don't say
    action: str  # "sell to open", "buy to close", "expired", ...
    contracts: int
    price: Decimal  # per contract, as the mid (or the expiry's value) it was filled at
    amount: Decimal  # dollars: money in positive, money out negative


def fills(session_factory, run_id: int) -> list[LegFill]:
    """A paper run's pretend fills leg by leg, in the order they happened; none for a run
    that isn't paper. The "action" rows (a rule that ran) fill nothing and are left out."""
    with session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or not run.paper:
            return []
        rows = list(session.scalars(
            select(PaperFill).where(PaperFill.run_id == run_id).order_by(PaperFill.at, PaperFill.id)
        ))  # fmt: skip
    legs = {leg["symbol"]: leg for leg in run.legs or ()}
    out = []
    for row in rows:
        if row.kind != ENTRY and row.kind not in caps.PAPER_CLOSING:
            continue
        for used in row.legs or ():
            leg = legs.get(used.get("symbol"), {})
            ratio = int(used.get("ratio") or leg.get("ratio") or 1)
            if row.kind == ENTRY or row.group is None:
                count = row.contracts * ratio
            else:
                count = row.contracts * ratio // caps.group_gcd(run.legs, row.group)
            sold = used.get("side") == "sell"
            price = Decimal(used.get("mid") or used.get("value") or 0)
            opening = row.kind == ENTRY
            sign = (1 if sold else -1) * (1 if opening else -1)
            if opening:
                action = "sell to open" if sold else "buy to open"
            elif row.kind == "expiry":
                action = "expired"
            else:
                action = "buy to close" if sold else "sell to close"
            out.append(LegFill(
                at=row.at, what=ENTRY if opening else row.reason, symbol=used.get("symbol", ""),
                leg=_leg_name(leg) or used.get("symbol", ""), action=action, contracts=count,
                price=price, amount=price * count * sign * run.multiplier,
            ))  # fmt: skip
    return out


def _leg_name(leg: dict) -> str | None:
    if leg.get("strike") is None or not leg.get("right"):
        return None
    return f"{leg['strike']} {leg['right']}"
