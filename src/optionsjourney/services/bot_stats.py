"""A bot's trading record (spec: bots slice 8, section 1; Tracker2's
strategy_stats.py and the bot strategy pages spec).

A bot's trades (decision 0071, #169) are the journal's positions linked to it on
its account (the runs it placed and the trades matched to it), plus the positions
carrying its tag on its account that no bot is linked to (trades opened by hand:
Tracker2 matches the subcategory and account; decision 0072 files by tag). A trade
linked to another bot is that bot's, even with the same tag. The numbers are the journal's own
(core.stats.journal_summary). One journal read serves the whole list."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from optionsjourney.bots.definition import BotDef
from optionsjourney.core.paper_pl import Totals
from optionsjourney.core.positions import Status
from optionsjourney.core.stats import JournalSummary, TradePoint, journal_summary, trade_curve
from optionsjourney.services import journal, paper_journal

_EPOCH = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Record:
    summary: JournalSummary
    open: list = field(default_factory=list)  # JournalEntry, newest opened first
    closed: list = field(default_factory=list)  # newest closed first


def _keys_of_bot(bot: BotDef) -> tuple[tuple, tuple]:
    account = f"{bot.broker}:{bot.account_number}"
    return ("bot", bot.id, account), ("tag", bot.tag_id, account)


def _keys_of_entry(entry) -> list[tuple]:
    """The groups a trade is in: its bot's alone when one is linked, else one per tag."""
    position = entry.position
    account = f"{position.broker}:{position.account_number}"
    if entry.bot is not None:  # placed by or matched to a bot: that bot's alone
        return [("bot", entry.bot.bot_id, account)]
    return [("tag", tag.id, account) for tag in entry.tags]


def _entries(groups: dict[tuple, list], bot: BotDef) -> list:
    linked, filed = _keys_of_bot(bot)
    return groups.get(linked, []) + (groups.get(filed, []) if bot.tag_id else [])


def _grouped(session_factory, cache) -> dict[tuple, list]:
    view = journal.load_journal(session_factory, cache=cache)
    groups: dict[tuple, list] = {}
    for entry in view.entries:
        for key in _keys_of_entry(entry):
            groups.setdefault(key, []).append(entry)
    return groups


@dataclass(frozen=True)
class Stats:
    summary: JournalSummary
    closed: int  # closed trades: what the win rate is out of
    paper: Totals | None = None  # a Paper bot's pretend trades, in place of the journal's
    # #208: the bot page's P/L graph, from the same trades: closed ones, from $0
    curve: list[TradePoint] = field(default_factory=list)


def _stats(entries) -> Stats:
    closed = [e.position for e in entries if e.position.status is Status.CLOSED]
    curve = trade_curve((p.closed_at, p.realized_pl) for p in closed if p.closed_at)
    return Stats(journal_summary(e.position for e in entries), len(closed), curve=curve)


def summaries(session_factory, bots, cache=None) -> dict[int, Stats]:
    """{bot id: its record's numbers}, from one journal read
    (strategy_stats_for_bots). Bots on the same tag and account share only the
    trades no bot is linked to."""
    groups = _grouped(session_factory, cache)
    return {
        bot.id: _paper_stats(session_factory, bot)
        if not bot.live
        else _stats(_entries(groups, bot))
        for bot in bots
    }


def _paper_stats(session_factory, bot: BotDef) -> Stats:
    """A Paper bot's record is its pretend trades alone (the Journal's Paper trades view), never
    the real trades carrying its tag."""
    trades = paper_journal.trades(session_factory, None, None, bot.id)
    totals = paper_journal.totals(trades)
    curve = trade_curve((t.closed_at, t.pl) for t in trades if t.pl is not None and t.closed_at)
    return Stats(journal_summary([]), sum(1 for t in trades if t.pl is not None), totals, curve)


def record(session_factory, bot: BotDef, cache=None) -> Record:
    entries = _entries(_grouped(session_factory, cache), bot)
    closed = [e for e in entries if e.position.status is Status.CLOSED]
    still_open = [e for e in entries if e.position.status is not Status.CLOSED]
    closed.sort(key=lambda e: e.position.closed_at or _EPOCH, reverse=True)
    still_open.sort(key=lambda e: e.position.opened_at, reverse=True)
    return Record(journal_summary(e.position for e in entries), still_open, closed)
