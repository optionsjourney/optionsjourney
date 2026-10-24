"""Finding a bot's past trades (spec: match past trades to bots, #164). The journal's
trades that look like the bot's (core.bot_match) are offered on the bot's Match trades
page; the ticked ones get the bot's tag the way the Journal tags them (journal.edit_many;
decision 0072 files by tag), which puts them in the bot's record (bot_stats). Nothing
changes until the user presses Match selected, and one batch can be undone: each trade
gets back its previous tags. #166: when the bot splits its
condors, a ticked whole condor is split as the Journal's Split button does and both halves
are filed; undo combines it again. A filed trade you haven't renamed takes the bot's name;
undo gives it back its own. #169 (decision 0071): Match selected also links the trade to
the bot (bot_trade_matches, by the key ticked; a condor split on match is linked whole and
its halves resolve through split_from), so the journal shows the bot and only its record
counts it; undo puts the links back too. Match all bots offers every unlinked trade to
its best-scoring live bot at once. #276: after a sync, auto_match links the trades Match all
bots would tick (a strong match to one bot), the way Tracker2 files a trade as it's
imported; a trade you unlinked, or whose match you undid, is never linked on its own
again (bot_auto_skips)."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from optionsjourney.bots.definition import BotDef, is_iron_condor_shape, sides_of
from optionsjourney.core import bot_match
from optionsjourney.core.bot_match import Match, Pattern, shape_of
from optionsjourney.db.models import BotAutoSkip, BotTradeMatch, TradeEdit, TradeTag
from optionsjourney.services import bot_store, journal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    entry: object  # journal.JournalEntry
    match: Match
    also: tuple[str, ...] = ()  # other bots it matches, by name
    splits: bool = False  # #166: filing splits it into a put and a call spread
    matched_to: str | None = None  # #169: the other bot it's matched to now, by name
    ticked: bool = False  # Match all bots: a strong match to this bot alone


@dataclass(frozen=True)
class Found:
    offered: list[Candidate] = field(default_factory=list)  # not filed yet, newest first
    filed_elsewhere: list[Candidate] = field(default_factory=list)  # another bot's


@dataclass(frozen=True)
class Batch:
    """What one Match selected changed: {position key: tag ids} before."""

    bot_id: int
    before: dict[str, frozenset[int]]
    split_before: dict[str, bool | None] = field(default_factory=dict)  # #166: condors split
    named: tuple[str, ...] = ()  # trades given the bot's name (they had no name of their own)
    links_before: dict[str, int | None] = field(default_factory=dict)  # #169: key -> bot id


@dataclass(frozen=True)
class Group:
    bot: BotDef
    candidates: list[Candidate]


@dataclass(frozen=True)
class FoundAll:
    """Match all bots (#169): each unlinked trade under its best-scoring bot."""

    groups: list[Group] = field(default_factory=list)
    accounts: dict = field(default_factory=dict)  # (broker, number) -> BrokerAccount


def pattern_of(bot: BotDef) -> Pattern:
    """The bot's definition as core.bot_match reads it."""
    groups = ()
    if bot.split_iron_condors and is_iron_condor_shape(bot):
        # #165: each journal half is one side (its put legs or its call legs), whatever
        # the grouping: a bot with a group per leg still matches a put and a call spread.
        groups = tuple(
            shape_of((leg.side, leg.right, leg.ratio) for leg in bot.legs if leg.group in side)
            for side in sides_of(bot)
        )  # fmt: skip
    days = frozenset(bot.entry_days) if bot.frequency == "weekly" else frozenset()
    dates = (
        frozenset(date.fromisoformat(d) for d in bot.specific_dates)
        if bot.frequency == "specific_dates"
        else frozenset()
    )
    return Pattern(
        bot.broker, bot.account_number, bot.underlying,
        shape_of((leg.side, leg.right, leg.ratio) for leg in bot.legs), groups,
        tuple(t.time for t in bot.entry_times if t.enabled), days, dates,
    )  # fmt: skip


def _offerable(entry, bot_id: int | None = None) -> bool:
    """Trades a bot placed keep their bot; stock trades are never a bot's. A trade matched
    to a bot by hand (#169) is offered to another bot (it can move), never to its own."""
    if journal.is_stock(entry.position):
        return False
    if entry.bot is None:
        return True
    return entry.bot.matched and bot_id is not None and entry.bot.bot_id != bot_id


def _splits(bot: BotDef, entry) -> bool:
    """#166: filing this trade under the bot splits it (a whole condor, the bot splitting)."""
    return bot.split_iron_condors and entry.position.can_split


def find(session_factory: sessionmaker, bot: BotDef, cache=None) -> Found:
    """The journal's trades that match the bot, newest opened first. Trades linked to it
    are left out (they're in its record); trades with its tag without a link (the #164
    way) are offered, to link them. A trade matched to another bot, or carrying the tag
    another bot files under (and not this one's), is listed apart: ticking it moves it."""
    view = journal.load_journal(session_factory, cache=cache)
    mine = pattern_of(bot)
    others = [
        (b.name, pattern_of(b))
        for b in bot_store.listing(session_factory)
        if b.id != bot.id and b.legs
    ]
    theirs = {b.tag_id for b in bot_store.listing(session_factory)} - {bot.tag_id}
    found = Found()
    for entry in sorted(view.entries, key=lambda e: e.position.opened_at, reverse=True):
        if not _offerable(entry, bot.id):
            continue
        tag_ids = {t.id for t in entry.tags}
        match = bot_match.score(mine, entry.position)
        if match is None:
            continue
        also = tuple(name for name, p in others if bot_match.score(p, entry.position))
        matched_to = entry.bot.name if entry.bot is not None else None
        other_bots = bot.tag_id not in tag_ids and bool(tag_ids & theirs)
        mine_already = not other_bots and matched_to is None
        listed = found.offered if mine_already else found.filed_elsewhere
        listed.append(Candidate(entry, match, also, _splits(bot, entry), matched_to))
    return found


def matchable_bots(session_factory: sessionmaker) -> list[BotDef]:
    """Match all bots: the live bots on the list with legs (not Paper, archived, deleted
    or a manual bot's hidden per-trade copy)."""
    return [
        b for b in bot_store.listing(session_factory)
        if b.live and not b.per_trade and b.legs
    ]  # fmt: skip


def find_all(session_factory: sessionmaker, cache=None) -> FoundAll:
    """Every unlinked trade under the bot it matches best (core.bot_match.best). A strong
    match to one bot is ticked; a tie at the top is listed under the first and names the
    others, unticked. Trades with a bot's tag but no link are offered too."""
    view = journal.load_journal(session_factory, cache=cache)
    bots = matchable_bots(session_factory)
    patterns = [(b, pattern_of(b)) for b in bots]
    by_bot: dict[int, list[Candidate]] = {}
    for entry in sorted(view.entries, key=lambda e: e.position.opened_at, reverse=True):
        if entry.bot is not None or not _offerable(entry):
            continue
        scored = [(b, m) for b, p in patterns if (m := bot_match.score(p, entry.position))]
        winners = bot_match.best(scored)
        if not winners:
            continue
        top = winners[0]
        match = next(m for b, m in scored if b is top)
        also = tuple(b.name for b in winners[1:])
        by_bot.setdefault(top.id, []).append(
            Candidate(entry, match, also, _splits(top, entry), None, match.strong and not also)
        )
    groups = [Group(b, by_bot[b.id]) for b in bots if b.id in by_bot]
    return FoundAll(groups, view.accounts)


def file(session_factory: sessionmaker, bot: BotDef, keys) -> Batch:
    """Give the trades the bot's tag, as the Journal does, and link them to it. Returns
    what they were before, for undo."""
    keys = list(dict.fromkeys(k for k in keys if k))
    linking = list(keys)  # #169: the keys ticked (a condor split here is linked whole)
    with session_factory() as session:
        links_before = {k: (m.bot_id if (m := session.get(BotTradeMatch, k)) else None)
                        for k in linking}  # fmt: skip
    split_before: dict[str, bool | None] = {}
    if bot.split_iron_condors:
        view = journal.load_journal(session_factory)
        whole = [k for k in keys if (e := journal.find_entry(view, k)) and _splits(bot, e)]
        with session_factory() as session:
            for k in whole:
                edit = session.get(TradeEdit, k)
                split_before[k] = edit.split if edit is not None else None
        for k in whole:
            journal.set_split(session_factory, k, True)  # the Journal's Split button
        keys = [h for k in keys for h in ((f"{k}#put", f"{k}#call") if k in whole else (k,))]
    with session_factory() as session:
        rows = {
            e.position_key: e
            for e in session.scalars(select(TradeEdit).where(TradeEdit.position_key.in_(keys)))
        }
        named = tuple(k for k in keys if k not in rows or not rows[k].name)
        tags: dict[str, set[int]] = {}
        for link in session.scalars(select(TradeTag).where(TradeTag.position_key.in_(keys))):
            tags.setdefault(link.position_key, set()).add(link.tag_id)
    before = {k: frozenset(tags.get(k, ())) for k in keys}
    journal.edit_many(session_factory, keys, add_tag_ids=[bot.tag_id])
    with session_factory() as session:  # unnamed trades take the bot's name
        for k in named:
            session.get(TradeEdit, k).name = bot.name
        now = datetime.now(UTC)
        for k in linking:
            link = session.get(BotTradeMatch, k)
            if link is None:
                session.add(BotTradeMatch(position_key=k, bot_id=bot.id, matched_at=now))
            else:
                link.bot_id, link.matched_at = bot.id, now
        session.commit()
    return Batch(bot.id, before, split_before, named, links_before)


def _skip(session, keys) -> None:
    """#276: a sync never links these trades to a bot on its own again."""
    now = datetime.now(UTC)
    for key in keys:
        if session.get(BotAutoSkip, key) is None:
            session.add(BotAutoSkip(position_key=key, skipped_at=now))


def undo(session_factory: sessionmaker, batch: Batch) -> int:
    """Put each trade's tags back as they were, and combine a condor filing
    split again. Returns how many. A trade it unlinks is never matched on its own again."""
    with session_factory() as session:
        _skip(session, [k for k, bot_id in batch.links_before.items() if bot_id is None])
        for key, tag_ids in batch.before.items():
            edit = session.get(TradeEdit, key)
            if edit is not None and key in batch.named:
                edit.name = None
            session.execute(delete(TradeTag).where(TradeTag.position_key == key))
            session.add_all(TradeTag(position_key=key, tag_id=t) for t in tag_ids)
        for key, split in batch.split_before.items():  # combined again, as it was
            edit = session.get(TradeEdit, key)
            if edit is not None:
                edit.split = split
        for key, bot_id in batch.links_before.items():  # #169: linked as it was
            link = session.get(BotTradeMatch, key)
            if bot_id is None:
                if link is not None:
                    session.delete(link)
            elif link is None:
                session.add(BotTradeMatch(position_key=key, bot_id=bot_id,
                                          matched_at=datetime.now(UTC)))  # fmt: skip
            else:
                link.bot_id = bot_id
        session.commit()
    return len(batch.before)


def file_all(session_factory: sessionmaker, ticked: dict[int, list[str]], bots) -> list[Batch]:
    """Match all bots' Match selected: each bot's ticked trades filed and linked, one
    batch per bot, undone together."""
    by_id = {b.id: b for b in bots}
    return [
        file(session_factory, by_id[bot_id], keys)
        for bot_id, keys in ticked.items()
        if bot_id in by_id and keys
    ]


def undo_all(session_factory: sessionmaker, batches: list[Batch]) -> int:
    """Undo a Match all bots batch, newest first. Returns how many trades."""
    return sum(undo(session_factory, b) for b in reversed(batches))


def unlink(session_factory: sessionmaker, key: str) -> str | None:
    """#177: take a matched trade off its bot for good (undo only lasts until a restart):
    the link goes, and the bot's tag comes off it (and off its halves, when split), so the
    bot's record no longer counts it. If it took the bot's name, it gets its own back.
    Returns the bot's name, or None when the trade wasn't matched."""
    with session_factory() as session:
        link = session.get(BotTradeMatch, key)
        if link is None:
            return None
        bot = bot_store.load(session_factory, link.bot_id)
        keys = (key, f"{key}#put", f"{key}#call")
        session.execute(
            delete(TradeTag).where(TradeTag.position_key.in_(keys), TradeTag.tag_id == bot.tag_id)
        )
        for edit in session.scalars(select(TradeEdit).where(TradeEdit.position_key.in_(keys))):
            if edit.name == bot.name:
                edit.name = None
        session.delete(link)
        _skip(session, [key])
        session.commit()
    return bot.name


def auto_match(session_factory: sessionmaker, cache=None) -> dict[str, int]:
    """#276: link the trades Match all bots would tick for you (a strong match to one bot
    only), leaving out trades you took off a bot (bot_auto_skips) and trades carrying
    another bot's tag. Returns {bot name: trades linked}."""
    found = find_all(session_factory, cache)
    with session_factory() as session:
        skipped = set(session.scalars(select(BotAutoSkip.position_key)))
    bot_tags = {b.tag_id for b in bot_store.listing(session_factory)}
    ticked: dict[int, list[str]] = {}
    for group in found.groups:
        for c in group.candidates:
            key = c.entry.position.key
            theirs = ({t.id for t in c.entry.tags} & bot_tags) - {group.bot.tag_id}
            if c.ticked and key not in skipped and not theirs:
                ticked.setdefault(group.bot.id, []).append(key)
    bots = [g.bot for g in found.groups]
    file_all(session_factory, ticked, bots)
    linked = {b.name: len(ticked[b.id]) for b in bots if b.id in ticked}
    for name, count in linked.items():
        logger.info("auto-match: %d trade(s) to %s", count, name)
    return linked
