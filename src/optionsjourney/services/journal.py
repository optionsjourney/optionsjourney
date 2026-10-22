"""The journal: positions with the user's edits, filters, dashboards, and the tags
trades are filed under (tags only: decision 0072)."""

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from optionsjourney.core import chain as core_chain
from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.calendar import (
    CurvePoint,
    DayPL,
    MonthGrid,
    MonthRow,
    by_month,
    daily_pl,
    equity_curve,
    max_drawdown,
    month_grid,
)
from optionsjourney.core.expiry import (
    estimate_expiration,
    is_settlement,
    settled_by_app,
    settlement_fill,
    with_settlements,
)
from optionsjourney.core.live import leg_keys, multipliers_from_fills, unrealized_pl
from optionsjourney.core.market import market_date
from optionsjourney.core.metrics import (
    PERIODS as METRIC_PERIODS,
)
from optionsjourney.core.metrics import (
    PeriodStats,
    TradeStats,
    WeekdayStats,
    period_stats,
    pl_by_period,
    trade_stats,
    weekday_stats,
)
from optionsjourney.core.orders import OrderGroup, group_fills
from optionsjourney.core.positions import Book, Position, Status, build_positions
from optionsjourney.core.stats import (
    JournalSummary,
    Summary,
    journal_summary,
    premium_of,
    summarize,
)
from optionsjourney.core.transactions import InstrumentType
from optionsjourney.db.models import (
    Account,
    Bot,
    BotExit,
    BotRun,
    BotTradeMatch,
    BrokerTransaction,
    Dashboard,
    Tag,
    TradeEdit,
    TradeTag,
)
from optionsjourney.services import expiry_settle
from optionsjourney.services.streaming import marks as hub_marks
from optionsjourney.services.transactions import FillsView, load_fills

CLOSE_REASONS = (
    "Profit target",
    "Stop loss",
    "Scheduled close",
    "Manual close",
    "Rolled",
    "Expired",
    "Assigned",
    "Exercised",
    "Matured",
    "Other",
)
PERIODS = {0: "All time", 7: "7 days", 30: "30 days", 90: "90 days", 365: "1 year"}
STATUSES = {"today": "Open + closed today", "all": "Open and closed", "open": "Open", "closed": "Closed"}
# The trade list opens on open trades plus the ones closed today; P/L and metrics on all (#68)
TRADES_STATUS = "today"
DEMO_TRADES_STATUS = "all"  # the demo opens on its whole made-up history


def trades_status(demo: bool) -> str:
    """The status the trade list opens on."""
    return DEMO_TRADES_STATUS if demo else TRADES_STATUS


UNCATEGORIZED = "none"  # the tag filter's "No tags"


class JournalError(Exception):
    """A request the journal can't carry out. The message is shown to the user."""


@dataclass(frozen=True)
class Filters:
    tags: tuple[str, ...] = ()  # tag ids, or UNCATEGORIZED (no tags)
    account: str = ""  # "broker:number"
    status: str = ""  # "" = not chosen: the page's default_status
    days: int = 0  # 0 = all time
    kind: str = ""  # "stocks": the Stocks tab
    q: str = ""  # search: name, underlying, tags, notes, or #order number
    start: date | None = None  # opened on or after (New York date)
    end: date | None = None  # opened on or before
    default_status: str = field(default="all", compare=False)

    @property
    def shown_status(self) -> str:
        """The status the trades are picked by: the one chosen, or the page's default."""
        return self.status or self.default_status

    @classmethod
    def from_args(cls, args, default_status: str = "all") -> Filters:
        """From a request's query string (a werkzeug MultiDict)."""
        days = args.get("days", 0, type=int)
        status = args.get("status", "")
        return cls(
            tags=tuple(v for v in args.getlist("tag") if v),
            account=args.get("account", ""),
            status=status if status in STATUSES else "",
            days=days if days in PERIODS else 0,
            kind="stocks" if args.get("kind") == "stocks" else "",
            q=" ".join((args.get("q") or "").split())[:100],
            start=_day(args.get("start")),
            end=_day(args.get("end")),
            default_status=default_status,
        )

    @classmethod
    def from_saved(cls, saved: Mapping) -> Filters:
        return cls(
            tags=tuple(saved.get("tag", ())),
            account=saved.get("account", ""),
            status=saved.get("status", ""),
            days=int(saved.get("days", 0)),
            kind="stocks" if saved.get("kind") == "stocks" else "",
            q=saved.get("q", ""),
            start=_day(saved.get("start")),
            end=_day(saved.get("end")),
        )

    def to_args(self) -> dict:
        """As query-string arguments, leaving defaults out."""
        args: dict = {}
        if self.tags:
            args["tag"] = list(self.tags)
        if self.account:
            args["account"] = self.account
        if self.status:
            args["status"] = self.status
        if self.days:
            args["days"] = self.days
        if self.kind:
            args["kind"] = self.kind
        if self.q:
            args["q"] = self.q
        if self.start:
            args["start"] = self.start.isoformat()
        if self.end:
            args["end"] = self.end.isoformat()
        return args


# A run whose rules were removed from its trade (manual bots, #158): it no longer manages
# it. bot_adopt.HANDED_BACK is this (bot_adopt imports this module).
HANDED_BACK = "handed"


@dataclass(frozen=True)
class BotLink:
    """The bot run that opened a trade, or (matched) the bot it was matched to by hand
    (#169): no run, so nothing to manage, only the bot's name and record."""

    bot_id: int
    name: str
    run_id: int | None
    per_trade: bool = False  # a manual bot's rules (#158): managed from Manage this trade
    matched: bool = False  # Match trades linked it (bot_trade_matches); no run


@dataclass
class JournalEntry:
    position: Position
    name: str
    tags: list[Tag]  # its own, plus Stocks on a stock trade and its bot's tag (0072)
    notes: str | None
    close_reason: str | None
    renamed: bool = False
    awaiting_broker: bool = False  # expired; the broker hasn't posted the record yet
    estimated_pl: Decimal | None = None  # what it made, from the brokers' closes (#23)
    bot: BotLink | None = None  # the bot that opened it, if one did
    settled_by_app: bool = False  # settled at the close; the broker hasn't confirmed (#147)
    settled_prelim: bool = False  # ...at the 4:15 price, not yet the official close (#186)
    prelim_note: str = ""  # the prelim's price and source, for its tooltip
    fixed_tags: list[Tag] = field(default_factory=list)  # in tags by rule (bot's, Stocks)


@dataclass
class JournalView:
    entries: list[JournalEntry] = field(default_factory=list)
    summary: Summary | None = None
    overview: JournalSummary | None = None  # the boxes above the trade list
    unmatched: list[OrderGroup] = field(default_factory=list)
    accounts: dict[tuple[str, str], BrokerAccount] = field(default_factory=dict)
    tags: list[Tag] = field(default_factory=list)
    dashboards: list[Dashboard] = field(default_factory=list)
    unreadable: int = 0
    failed: int = 0  # orders set aside after an error building positions


class PositionsCache:
    """The positions built from all stored transactions, kept between page
    loads. Building them means reading and parsing every transaction, so it's
    redone only when the transactions change (a sync added or updated some)
    or the user's iron condor split choices change. One per app."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._value: tuple[FillsView, Book] | None = None

    def clear(self) -> None:
        """Forget the built positions, e.g. after the database is restored."""
        with self._lock:
            self._key = self._value = None

    def get(self, session_factory: sessionmaker, split: Iterable[str]) -> tuple[FillsView, Book]:
        owners = close_owners(session_factory)
        key = (_transactions_version(session_factory), frozenset(split), frozenset(owners.items()))
        with self._lock:
            if self._key != key or self._value is None:
                self._value = _build(session_factory, split, owners)
                self._key = key
            return self._value


def bot_links(session_factory: sessionmaker) -> dict[str, tuple[BotLink, Bot]]:
    """Position key -> the bot run that opened it (spec: the first automation,
    section 3). Built on read, like trade edits."""
    with session_factory() as session:
        rows = session.execute(
            select(BotRun, Bot)
            .join(Bot, Bot.id == BotRun.bot_id)
            .where(BotRun.broker_order_id.is_not(None), BotRun.status != HANDED_BACK)
        ).all()
        return {
            f"{bot.broker}:{bot.account_number}:{run.broker_order_id}": (
                BotLink(bot.id, bot.name, run.id, bool(bot.per_trade)),
                bot,
            )
            for run, bot in rows
        }


def matched_links(session_factory: sessionmaker) -> dict[str, tuple[BotLink, Bot]]:
    """Position key -> the bot a past trade was matched to (#169, bot_trade_matches)."""
    with session_factory() as session:
        rows = session.execute(
            select(BotTradeMatch, Bot).join(Bot, Bot.id == BotTradeMatch.bot_id)
        ).all()
        return {
            match.position_key: (BotLink(bot.id, bot.name, None, False, True), bot)
            for match, bot in rows
        }


def _split_keys(edits, links) -> set[str]:
    """Positions shown split. Your choice wins; a bot that splits its condors
    does so unless you combined this one."""
    split = {key for key, edit in edits.items() if edit.split}
    split |= {
        key
        for key, (_, bot) in links.items()
        if bot.split_iron_condors and (key not in edits or edits[key].split is None)
    }
    return split


def _transactions_version(session_factory: sessionmaker) -> tuple:
    """Changes whenever a transaction is added or updated, or an expiration
    is settled at the close (#147)."""
    with session_factory() as session:
        transactions = tuple(
            session.execute(select(func.count(), func.max(BrokerTransaction.updated_at))).one()
        )
    return transactions + expiry_settle.version(session_factory)


def close_owners(session_factory: sessionmaker) -> dict[str, str]:
    """Closing order id -> the key of the bot trade whose exit sent it (#180): a bot's stop
    closes its own trade even when an older trade holds the same strikes."""
    with session_factory() as session:
        rows = session.execute(
            select(BotExit.broker_order_id, BotRun.broker_order_id, Bot.broker, Bot.account_number)
            .join(BotRun, BotRun.id == BotExit.run_id)
            .join(Bot, Bot.id == BotRun.bot_id)
            .where(BotExit.broker_order_id.is_not(None), BotRun.broker_order_id.is_not(None),
                   BotRun.status != HANDED_BACK)  # fmt: skip
        ).all()
    return {str(exit_order): f"{broker}:{account}:{run_order}"
            for exit_order, run_order, broker, account in rows}  # fmt: skip


def _build(
    session_factory: sessionmaker, split: Iterable[str], owners: dict[str, str] | None = None
) -> tuple[FillsView, Book]:
    """Positions from the fills plus the app's settlements the brokers haven't
    replaced yet (#147). The FillsView itself holds only the brokers' fills."""
    fills = load_fills(session_factory)
    settlements = expiry_settle.load_settlements(session_factory)
    merged = with_settlements(fills.fills, settlements)
    if owners is None:
        owners = close_owners(session_factory)
    return fills, build_positions(group_fills(merged), split=split, owners=owners)


def build_unsplit(session_factory: sessionmaker) -> tuple[FillsView, Book]:
    """Every position with no iron condor split, freshly built: handing a trade to a
    bot (bots slice 12) takes the whole condor, whichever way the journal shows it."""
    return _build(session_factory, ())


def _prelim_note(prelims) -> str:
    """The "(prelim)" tag's tooltip: the price each prelim settlement used, and where it
    came from (#186)."""
    if not prelims:
        return ""
    prices = sorted({(s.underlying, s.close, s.source) for s in prelims})
    used = "; ".join(
        f"{underlying} {close}" + (f" ({source})" if source else "")
        for underlying, close, source in prices
    )
    return (
        f"Settled at {used} at 4:15 PM. It's corrected to the official close when that's "
        "out, and you get an alert if the P/L changes by $1 or more."
    )


def load_journal(
    session_factory: sessionmaker,
    filters: Filters | None = None,
    now: datetime | None = None,
    *,
    cache: PositionsCache | None = None,
) -> JournalView:
    """Every position with its edits, filtered, newest first."""
    filters = filters or Filters()
    now = now or datetime.now(UTC)
    with session_factory() as session:
        tags = list(session.scalars(select(Tag).order_by(func.lower(Tag.name))))
        edits = {edit.position_key: edit for edit in session.scalars(select(TradeEdit))}
        tags_by_key: dict[str, list[Tag]] = {}
        tags_by_id = {tag.id: tag for tag in tags}
        for link in session.scalars(select(TradeTag)):
            tags_by_key.setdefault(link.position_key, []).append(tags_by_id[link.tag_id])
        dashboards = list(session.scalars(select(Dashboard).order_by(Dashboard.sort_order)))

    links = bot_links(session_factory)
    matched = matched_links(session_factory)
    split = _split_keys(edits, links)
    if cache is not None:
        fills, book = cache.get(session_factory, split)
    else:
        fills, book = _build(session_factory, split)

    # Stock trades always carry the Stocks tag (while it exists; migration 0008 adds it)
    stocks_tag = next((t for t in tags if t.name.lower() == STOCKS_TAG.lower()), None)
    # Keyed by account too: two accounts settling one contract share a transaction id
    prelims = {
        (s.broker, s.account_number, settlement_fill(s).transaction_id): s
        for s in expiry_settle.load_settlements(session_factory)
        if s.status == "prelim"
    }
    entries = []
    for position in book.positions:
        edit = edits.get(position.key)
        own_tags = tags_by_key.get(position.key, [])
        if position.split_from and edit is None and not own_tags:
            # A half you haven't edited keeps the whole trade's tags (#166 follow-up)
            own_tags = tags_by_key.get(position.split_from, [])
        fixed = []
        if stocks_tag is not None and is_stock(position):
            fixed.append(stocks_tag)
        linked = (
            links.get(position.split_from or position.key)
            or matched.get(position.key)
            or (matched.get(position.split_from) if position.split_from else None)
        )
        # A bot's trades carry the tag it files under (decision 0072), as stock trades
        # carry Stocks
        # A deleted bot's tag is already on its trades as their own (0074)
        bot_tag = (
            tags_by_id.get(linked[1].tag_id)
            if linked is not None and not linked[1].hidden
            else None
        )
        if bot_tag is not None and bot_tag not in fixed:
            fixed.append(bot_tag)
        own_tags = [*own_tags, *(t for t in fixed if t not in own_tags)]
        prelim = (
            [
                prelims[(f.broker, f.account_number, f.transaction_id)]
                for event in position.events
                for order in event.orders
                for f in order.fills
                if is_settlement(f) and (f.broker, f.account_number, f.transaction_id) in prelims
            ]
            if prelims
            else []
        )
        entries.append(
            JournalEntry(
                position=position,
                # A bot's trade shows the bot's name until it's renamed, as a
                # matched trade takes it (bot_match.apply)
                name=(edit.name if edit and edit.name
                      else linked[0].name if linked else position.name),
                renamed=bool(edit and edit.name),
                bot=linked[0] if linked else None,
                tags=sorted(own_tags, key=lambda t: t.name.lower()),
                notes=edit.notes if edit else None,
                close_reason=(edit.close_reason if edit and edit.close_reason else None)
                or position.close_reason,
                awaiting_broker=position.awaiting_expiration(now),
                settled_by_app=settled_by_app(position),
                settled_prelim=bool(prelim),
                prelim_note=_prelim_note(prelim),
                fixed_tags=fixed,
            )
        )

    shown = [e for e in entries if _matches(e, filters, now)]
    shown.sort(key=opened_key, reverse=True)
    unmatched = [
        order
        for order in book.unmatched
        if not filters.account or f"{order.broker}:{order.account_number}" == filters.account
    ]
    return JournalView(
        entries=shown,
        summary=summarize(e.position for e in shown),
        overview=journal_summary(
            (e.position for e in shown),
            day=market_date(now) if filters.shown_status == "today" else None,
        ),
        unmatched=sorted(unmatched, key=lambda o: o.executed_at, reverse=True),
        accounts=fills.accounts,
        tags=tags,
        dashboards=dashboards,
        unreadable=fills.unreadable,
        failed=len(book.failed),
    )


def find_entry(view: JournalView, key: str) -> JournalEntry | None:
    return next((e for e in view.entries if e.position.key == key), None)


def opened_key(entry: JournalEntry) -> tuple:
    """Sorting by when trades opened (#214, Tracker2's Opened sort): trades opened in
    the same minute group by entry (account, bot, underlying) before the exact time
    orders them, so two accounts' put and call sides opened seconds apart don't
    interleave. Either direction keeps an entry's sides next to each other."""
    p = entry.position
    entry_of = (p.broker, p.account_number, entry.bot.name if entry.bot else "", p.underlying)
    return (p.opened_at.replace(second=0, microsecond=0), entry_of, p.opened_at)


# Columns the trade list sorts by (#60): name -> the value to sort on
SORTS = {
    "opened": opened_key,
    "account": lambda e: (e.position.broker, e.position.account_number),
    "trade": lambda e: e.name.lower(),
    "status": lambda e: e.position.status.value,
    "qty": lambda e: e.position.units,
    "entry": lambda e: e.position.entry_price,
    "exit": lambda e: e.position.exit_price,
    "pl": lambda e: e.position.realized_pl if e.position.status is not Status.OPEN else None,
    "premium": lambda e: premium_of(e.position) or None,
    "fees": lambda e: e.position.fees,
    "closed": lambda e: e.position.closed_at,
}


@dataclass
class RowHeader:
    """A heading row in the trade list (Tracker2's Open / Closed cards and its
    closed-by-day headers): kind is "open", "closed" or "day"."""

    kind: str
    count: int
    pl: Decimal | None = None
    day: date | None = None


def _closed(entry: JournalEntry) -> bool:
    return entry.position.status == Status.CLOSED


def _row_pl(entry: JournalEntry) -> Decimal:
    if entry.estimated_pl is not None:
        return entry.estimated_pl
    return entry.position.realized_pl or Decimal(0)


def open_then_closed(entries: list[JournalEntry]) -> list[JournalEntry]:
    """The default order (Tracker2's): open trades newest first, then closed ones
    by the day they closed, newest first, and within a day by when they opened,
    newest first. An entry's sides stay together (opened_key)."""
    still_open = sorted((e for e in entries if not _closed(e)), key=opened_key, reverse=True)
    closed = sorted(
        (e for e in entries if _closed(e)),
        key=lambda e: (
            market_date(e.position.closed_at or e.position.opened_at),
            opened_key(e),
        ),
        reverse=True,
    )
    return still_open + closed


def with_headers(entries: list[JournalEntry], shown: list[JournalEntry]) -> list:
    """`shown` (one page of `entries`, in open_then_closed order) with a RowHeader
    before the open trades, before the closed ones, and before each closing day.
    Counts and P/L are over all of `entries`, not just the page."""
    open_count = sum(1 for e in entries if not _closed(e))
    closed = [e for e in entries if _closed(e)]
    days: dict[date, list[JournalEntry]] = {}
    for e in closed:
        days.setdefault(market_date(e.position.closed_at or e.position.opened_at), []).append(e)
    rows: list = []
    section = day = None
    for e in shown:
        if not _closed(e):
            if section != "open":
                rows.append(RowHeader("open", open_count))
                section = "open"
        else:
            if section != "closed":
                rows.append(RowHeader("closed", len(closed), sum(map(_row_pl, closed), Decimal(0))))
                section = "closed"
            d = market_date(e.position.closed_at or e.position.opened_at)
            if d != day:
                group = days[d]
                rows.append(RowHeader("day", len(group), sum(map(_row_pl, group), Decimal(0)), d))
                day = d
        rows.append(e)
    return rows


def sort_entries(entries: list[JournalEntry], column: str, descending: bool) -> list[JournalEntry]:
    """Sorted by a column; trades with no value there (an open trade's exit)
    go last whichever way. Unknown columns sort newest first."""
    if column not in SORTS:
        column, descending = "opened", True
    value = SORTS[column]
    known = [e for e in entries if value(e) is not None]
    missing = [e for e in entries if value(e) is None]
    return sorted(known, key=value, reverse=descending) + missing


def _day(text) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except TypeError, ValueError:
        return None


def _searched(entry: JournalEntry, text: str) -> bool:
    """Every word must appear in the trade's name, underlying, tags
    or notes. "#123" matches the broker's order number."""
    if text.startswith("#"):
        wanted = text[1:].strip()
        return any(
            order.order_id == wanted for event in entry.position.events for order in event.orders
        )
    haystack = " ".join(
        [
            entry.name,
            entry.position.name,
            entry.position.underlying,
            " ".join(t.name for t in entry.tags),
            entry.notes or "",
        ]
    ).lower()
    return all(word in haystack for word in text.lower().split())


def analyzable(entry: JournalEntry) -> bool:
    """An open trade with options still held: Analyze can model it (#62)."""
    position = entry.position
    return (
        position.status is not Status.CLOSED
        and not entry.awaiting_broker
        and any(leg.option is not None and leg.remaining > 0 for leg in position.live_legs)
    )


STOCKS_TAG = "Stocks"


def is_stock(position) -> bool:
    return all(leg.instrument_type is InstrumentType.STOCK for leg in position.legs)


def _matches(entry: JournalEntry, filters: Filters, now: datetime) -> bool:
    position = entry.position
    if filters.account and f"{position.broker}:{position.account_number}" != filters.account:
        return False
    is_closed = position.status is Status.CLOSED
    if filters.shown_status == "open" and is_closed:
        return False
    if filters.shown_status == "closed" and not is_closed:
        return False
    if filters.shown_status == "today" and is_closed and (
        position.closed_at is None or market_date(position.closed_at) != market_date(now)
    ):
        return False
    if filters.tags:
        # Any of the picked tags; "No tags" picks trades with none
        have = {str(t.id) for t in entry.tags} or {UNCATEGORIZED}
        if not have & set(filters.tags):
            return False
    if filters.kind == "stocks" and not is_stock(position):
        return False
    if filters.q and not _searched(entry, filters.q):
        return False
    opened = market_date(position.opened_at)
    if filters.start and opened < filters.start:
        return False
    if filters.end and opened > filters.end:
        return False
    if filters.days:
        since = now - timedelta(days=filters.days)
        # Still open, or opened or closed in the period
        if is_closed and position.opened_at < since and position.closed_at < since:
            return False
    return True


# --- Estimating expired trades ----------------------------------------------------


def estimate_expired(view: JournalView, closes) -> None:
    """Fill in estimated P/L on trades waiting on the broker's expiration
    record. `closes` maps a set of (underlying, day) to their closing prices
    (services.market_data.CloseCache.closes, bound to the brokers)."""
    waiting = [e for e in view.entries if e.awaiting_broker]
    wanted = {
        (leg.underlying, leg.option.expiration)
        for e in waiting
        for leg in e.position.live_legs
        if leg.remaining > 0 and leg.option is not None
    }
    if not wanted:
        return
    prices = closes(wanted)
    for entry in waiting:
        entry.estimated_pl = estimate_expiration(entry.position, prices)


# --- Live open P/L (#24) -----------------------------------------------------------


def live_open_pl(view: JournalView, hub) -> dict:
    """Open P/L of each open trade in the view, from the quote hub's marks.
    Expired trades waiting on the broker are left out (they have estimates)."""
    entries = [
        e for e in view.entries if e.position.status is not Status.CLOSED and not e.awaiting_broker
    ]
    keys = set().union(*(leg_keys(e.position) for e in entries)) if entries else set()
    hub.subscribe(keys)
    marks = hub_marks(hub, keys)  # after hours, a pulled market at its last real mark (#241)
    multipliers = multipliers_from_fills(
        fill
        for e in entries
        for event in e.position.events
        for order in event.orders
        for fill in order.fills
    )
    values = {e.position.key: unrealized_pl(e.position, marks, multipliers) for e in entries}
    known = [v for v in values.values() if v is not None]
    return {
        "positions": values,
        "marks": marks,
        "total": sum(known, Decimal("0")) if known else None,
        "priced": len(known),
        "open": len(entries),
    }


def open_pl_of(session_factory, cache, hub, key: str) -> Decimal | None:
    """One trade's live open P/L (for its alerts), or None."""
    view = load_journal(session_factory, cache=cache)
    entry = find_entry(view, key)
    if entry is None or entry.position.status is Status.CLOSED:
        return None
    return live_open_pl(JournalView(entries=[entry]), hub)["positions"].get(key)


def held_legs(
    session_factory, ticker: str, now: datetime | None = None, *, cache=None
) -> list[dict]:
    """The Trade page chain's Pos column (#210): the journal's open trades on the
    ticker, as net contracts per account id, expiration, strike and call/put
    (core.chain.held_legs). Trades past expiry waiting on the broker are left out."""
    view = load_journal(session_factory, now=now, cache=cache)
    with session_factory() as session:
        ids = {(a.broker, a.number): a.id for a in session.scalars(select(Account))}
    positions = [
        e.position
        for e in view.entries
        if e.position.status is not Status.CLOSED and not e.awaiting_broker
    ]
    return core_chain.held_legs(positions, ids, ticker)


# --- The P/L calendar ----------------------------------------------------------


def parse_month(text: str | None, now: datetime | None = None) -> date:
    """The first of the month named "YYYY-MM", or of this month."""
    try:
        year, month = (int(part) for part in (text or "").split("-"))
        return date(year, month, 1)
    except ValueError:
        return market_date(now or datetime.now(UTC)).replace(day=1)


def pl_by_day(view: JournalView) -> dict[date, DayPL]:
    """Realized P/L per day over the trades the view shows."""
    return daily_pl(e.position for e in view.entries)


def load_calendar(days: dict[date, DayPL], month: date) -> MonthGrid:
    return month_grid(month.year, month.month, days)


@dataclass(frozen=True)
class PLHistory:
    points: list[CurvePoint]
    months: list[MonthRow]  # newest first, for the table view
    deepest: CurvePoint | None  # the deepest drawdown

    def chart_data(self) -> list[dict]:
        """The points as plain JSON for the page's chart script."""
        return [
            {
                "day": p.day.isoformat(),
                "pl": float(p.pl),
                "cumulative": float(p.cumulative),
                "drawdown": float(p.drawdown),
            }
            for p in self.points
        ]


def load_history(days: dict[date, DayPL]) -> PLHistory:
    """Cumulative P/L and drawdown over time."""
    curve = equity_curve(days)
    return PLHistory(curve, list(reversed(by_month(curve))), max_drawdown(curve))


# --- Trading metrics -----------------------------------------------------------


@dataclass(frozen=True)
class Metrics:
    realized: Decimal  # all realized P/L, partial closes included
    realized_partial: Decimal  # the part from trades still open
    trades: TradeStats  # closed trades
    max_drawdown: CurvePoint | None
    periods: list[PeriodStats]  # day, week, month
    weekdays: list[WeekdayStats]
    bars: list[dict]  # P/L per `period`, for the chart

    @property
    def realized_closed(self) -> Decimal:
        return self.realized - self.realized_partial


def load_metrics(view: JournalView, period: str) -> Metrics:
    """Trading metrics over the trades the view shows; `period` is what the
    P/L bars are grouped by."""
    days = pl_by_day(view)
    closed = [e.position for e in view.entries if e.position.status is Status.CLOSED]
    realized = sum((e.position.realized_pl for e in view.entries), Decimal("0"))
    return Metrics(
        realized=realized,
        realized_partial=realized - sum((p.realized_pl for p in closed), Decimal("0")),
        trades=trade_stats((p.realized_pl for p in closed), (p.fees for p in closed)),
        max_drawdown=max_drawdown(equity_curve(days)),
        periods=[period_stats(days, name) for name in METRIC_PERIODS],
        weekdays=weekday_stats(days),
        bars=[
            {"start": start.isoformat(), "pl": float(pl)}
            for start, pl in pl_by_period(days, period).items()
        ],
    )


# --- Editing a trade -----------------------------------------------------------


def save_trade(
    session_factory: sessionmaker,
    key: str,
    *,
    name: str,
    tags: str,
    notes: str,
    close_reason: str,
) -> None:
    """Save the user's edits to one trade. Blank fields go back to the default."""
    if close_reason and close_reason not in CLOSE_REASONS:
        raise JournalError(f"Unknown close reason: {close_reason}")
    with session_factory() as session:
        edit = _edit(session, key)
        edit.name = name.strip() or None
        edit.notes = notes.strip() or None
        edit.close_reason = close_reason or None
        edit.updated_at = datetime.now(UTC)
        session.execute(delete(TradeTag).where(TradeTag.position_key == key))
        for tag in _tags(session, tags):
            session.add(TradeTag(position_key=key, tag_id=tag.id))
        session.commit()


def edit_many(
    session_factory: sessionmaker,
    keys: Iterable[str],
    *,
    add_tags: str = "",
    remove_tags: str = "",
    add_tag_ids: Iterable[int] = (),
) -> int:
    """Add or remove tags on several trades at once (the journal's ticked trades,
    or a bot's Match trades with its tag by id). Other fields and other tags stay.
    Returns how many trades were changed."""
    keys = list(dict.fromkeys(k for k in keys if k))
    with session_factory() as session:
        adding = _tags(session, add_tags)
        for tag_id in add_tag_ids:
            tag = session.get(Tag, tag_id)
            if tag is None:
                raise JournalError("That tag no longer exists.")
            adding.append(tag)
        removing = {" ".join(n.split()).lower() for n in remove_tags.split(",")} - {""}
        tag_names = {t.id: t.name.lower() for t in session.scalars(select(Tag))}
        for key in keys:
            edit = _edit(session, key)
            edit.updated_at = datetime.now(UTC)
            have = set(session.scalars(select(TradeTag.tag_id).where(TradeTag.position_key == key)))
            for tag in adding:
                if tag.id not in have and tag.name.lower() not in removing:
                    session.add(TradeTag(position_key=key, tag_id=tag.id))
                    have.add(tag.id)
            gone = [tid for tid in have if tag_names.get(tid) in removing]
            if gone:
                session.execute(
                    delete(TradeTag).where(TradeTag.position_key == key, TradeTag.tag_id.in_(gone))
                )
        session.commit()
    return len(keys)


def set_split(session_factory: sessionmaker, key: str, split: bool) -> None:
    """Your choice for an iron condor: True splits it, False keeps it combined
    (even if its bot splits its condors). With no choice stored, the default
    applies."""
    with session_factory() as session:
        edit = _edit(session, key)
        edit.split = split
        edit.updated_at = datetime.now(UTC)
        session.commit()


def _edit(session: Session, key: str) -> TradeEdit:
    edit = session.get(TradeEdit, key)
    if edit is None:
        edit = TradeEdit(position_key=key, updated_at=datetime.now(UTC))
        session.add(edit)
    return edit


def _tags(session: Session, text: str) -> list[Tag]:
    """The tags named in "earnings, A+ setup", created if new. Names match
    existing tags regardless of case."""
    existing = {tag.name.lower(): tag for tag in session.scalars(select(Tag))}
    result: dict[str, Tag] = {}
    for raw in text.split(","):
        name = " ".join(raw.split())[:60]
        if not name or name.lower() in result:
            continue
        tag = existing.get(name.lower())
        if tag is None:
            tag = Tag(name=name)
            session.add(tag)
            session.flush()
            existing[name.lower()] = tag
        result[name.lower()] = tag
    return list(result.values())


# --- Tags --------------------------------------------------------------------


def add_tag(session_factory: sessionmaker, name: str) -> Tag:
    """A new tag (the Tags page). A name already taken, any case, is refused."""
    name = _tag_name(name)
    with session_factory() as session:
        if _tag_named(session, name):
            raise JournalError(f"There's already a tag called {name}.")
        tag = Tag(name=name)
        session.add(tag)
        session.commit()
        session.refresh(tag)
        session.expunge(tag)
        return tag


def tag_for(session_factory: sessionmaker, name: str) -> Tag:
    """The tag with this name (any case), made if new: a bot's "+ New tag"."""
    name = _tag_name(name)
    with session_factory() as session:
        tag = _tags(session, name)[0]
        session.commit()
        session.refresh(tag)
        session.expunge(tag)
        return tag


def rename_tag(session_factory: sessionmaker, tag_id: int, name: str) -> None:
    name = _tag_name(name)
    with session_factory() as session:
        tag = session.get(Tag, tag_id)
        if tag is None:
            raise JournalError("That tag no longer exists.")
        other = _tag_named(session, name)
        if other is not None and other.id != tag_id:
            raise JournalError(f"There's already a tag called {name}.")
        tag.name = name
        session.commit()


def _tag_named(session: Session, name: str) -> Tag | None:
    return session.scalars(select(Tag).where(func.lower(Tag.name) == name.lower())).first()


def delete_tag(session_factory: sessionmaker, tag_id: int) -> None:
    """Delete a tag; it comes off every trade. A tag a bot files under can't go while
    the bot exists (decision 0072)."""
    with session_factory() as session:
        bots = list(session.scalars(select(Bot.name).where(Bot.tag_id == tag_id)))
        if bots:
            names = ", ".join(sorted(bots))
            raise JournalError(
                f"This tag can't be deleted: {'a bot files' if len(bots) == 1 else 'bots file'} "
                f"its trades under it ({names}). Give the bot another tag first."
            )
        session.execute(delete(Tag).where(Tag.id == tag_id))
        session.commit()


def all_tags(session_factory: sessionmaker) -> list[Tag]:
    with session_factory() as session:
        return list(session.scalars(select(Tag).order_by(func.lower(Tag.name))))


def tag_counts(session_factory: sessionmaker) -> dict[int, int]:
    """{tag id: how many trades you tagged with it} (not counting Stocks or a bot's
    tag, which the journal adds as it shows the trades)."""
    with session_factory() as session:
        rows = session.execute(select(TradeTag.tag_id, func.count()).group_by(TradeTag.tag_id))
        return dict(rows.all())


def bot_tags(session_factory: sessionmaker) -> dict[int, list[tuple[int, str, bool]]]:
    """{tag id: (id, name, deleted) of each bot that files under it}. A deleted bot
    (hidden, kept for its trades' links) still holds its tag (0074)."""
    with session_factory() as session:
        found: dict[int, list[tuple[int, str, bool]]] = {}
        rows = session.execute(select(Bot.tag_id, Bot.id, Bot.name, Bot.hidden).order_by(Bot.name))
        for tag_id, bot_id, name, hidden in rows:
            found.setdefault(tag_id, []).append((bot_id, name, bool(hidden)))
        return found


def keep_bot_tag(session_factory: sessionmaker, bot_id: int) -> None:
    """Before a bot is deleted (hidden): its tag goes onto each of its trades as their
    own, so they keep it and it can be taken off like any tag (0074)."""
    with session_factory() as session:
        bot = session.get(Bot, bot_id)
        tag_id = bot.tag_id if bot is not None else None
    if tag_id is None:
        return
    keys = [
        key
        for links in (bot_links(session_factory), matched_links(session_factory))
        for key, (link, _) in links.items()
        if link.bot_id == bot_id
    ]
    edit_many(session_factory, keys, add_tag_ids=[tag_id])


def _tag_name(name: str) -> str:
    name = _clean_name(name)
    if "," in name:
        raise JournalError("A tag's name can't have a comma in it.")
    return name


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise JournalError("Give it a name.")
    if len(name) > 60:
        raise JournalError("Names can be up to 60 characters.")
    return name


# --- Dashboards ----------------------------------------------------------------


def create_dashboard(session_factory: sessionmaker, name: str, filters: Filters) -> int:
    name = _clean_name(name)
    with session_factory() as session:
        last = session.scalar(select(func.max(Dashboard.sort_order))) or 0
        dashboard = Dashboard(
            name=name,
            sort_order=last + 1,
            filters=filters.to_args(),
            created_at=datetime.now(UTC),
        )
        session.add(dashboard)
        session.commit()
        return dashboard.id


def update_dashboard(session_factory: sessionmaker, dashboard_id: int, filters: Filters) -> None:
    with session_factory() as session:
        dashboard = session.get(Dashboard, dashboard_id)
        if dashboard is None:
            raise JournalError("That dashboard no longer exists.")
        dashboard.filters = filters.to_args()
        session.commit()


def delete_dashboard(session_factory: sessionmaker, dashboard_id: int) -> None:
    with session_factory() as session:
        session.execute(delete(Dashboard).where(Dashboard.id == dashboard_id))
        session.commit()


def get_dashboard(session_factory: sessionmaker, dashboard_id: int) -> Dashboard | None:
    with session_factory() as session:
        return session.get(Dashboard, dashboard_id)
