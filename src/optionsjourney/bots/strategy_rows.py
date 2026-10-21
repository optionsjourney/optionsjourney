"""The Automation list's rows (#216; Tracker2's strategies page, 81d4475c and
services/bots/account_variants.py). Pure.

Bots sit under their strategy: the journal tag their trades are filed under
(decision 0072, which replaced strategies with tags). Within a strategy, one bot
kept per account ("Iron Condor - Beta" in the Beta account, "Iron Condor - IRA" in the IRA)
draws as one row with an account chip per bot. Each stays its own bot, with its
own size, times, arming and mode; the row only adds up what adds up.

The rule is Tracker2's: a trailing " - <suffix>" is dropped from the name when
every word of the suffix is a word of that bot's own account name ("IRA" on
"Roth IRA ••1234"). A suffix that names something else ("Butterfly - XSP") stays, so
unrelated bots never merge. Bots with the same name under the same strategy are
one row as well; bots under different strategies never are."""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from optionsjourney.bots import floating, schedule
from optionsjourney.bots import summary as bot_summary
from optionsjourney.bots.definition import BotDef

_WORD = re.compile(r"[0-9A-Za-z]+")
WEEKDAYS = (1, 2, 3, 4, 5)
NO_STRATEGY = "No strategy"
OFF_GROUP = "Off"


def _words(text: str | None) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def base_name(name: str, account: str | None) -> str:
    """The bot's name without a trailing " - <its account>" suffix."""
    name = (name or "").strip()
    head, sep, suffix = name.rpartition(" - ")
    if not sep or not head.strip():
        return name
    account_words = set(_words(account))
    suffix_words = _words(suffix)
    if suffix_words and account_words and set(suffix_words) <= account_words:
        return head.strip()
    return name


@dataclass(frozen=True)
class Member:
    """One bot in a row: the bot, its account's name and its record's P/L."""

    bot: BotDef
    account: str
    times: tuple[str, ...]  # its entry times ("09:47"), or its floating window
    pl: Decimal | None  # its record's closed P/L, None before its first closed trade
    paper_pl: bool  # pl is a Paper bot's pretend trades


@dataclass(frozen=True)
class Row:
    key: str  # its bots' ids, "3-7"
    name: str
    strategy: str  # the journal tag its bots file under
    members: tuple[Member, ...]
    underlying: str
    armed: bool
    live: bool  # an armed bot in it is Live
    paper: bool  # armed, and every armed bot in it is Paper
    archived: bool
    lots: int | None  # contracts per entry across its accounts; None: all sized in dollars
    lots_partial: bool  # some bots size in dollars, so they aren't in lots
    pl: Decimal | None  # real trades
    paper_pl: Decimal | None  # Paper bots' pretend trades
    pl_shared: bool  # two of its bots share an account, so their records overlap: no sum
    weekly: bool  # fires on fixed weekdays: drawn as a Mon–Fri strip
    days: tuple[int, ...]  # those weekdays, 1 = Monday
    days_label: str
    shared_times: bool
    first_time: str
    legs: tuple[str, ...]
    legs_differ: bool
    exits: tuple[str, ...]
    tags: tuple[str, ...]  # the bots' own labels

    @property
    def multi(self) -> bool:
        return len(self.members) > 1


def _times(bot: BotDef) -> tuple[str, ...]:
    if bot.use_floating_entry:
        return (floating.label(bot),)
    return tuple(t.time[:5] for t in bot.entry_times if t.enabled)


def _days(bot: BotDef) -> tuple[int, ...] | None:
    """Its weekdays when it fires on fixed weekdays (daily is Monday to Friday), else None."""
    if bot.frequency == "weekly":
        return tuple(sorted({d for d in bot.entry_days if d in WEEKDAYS}))
    if bot.frequency == "daily":
        return WEEKDAYS
    return None


def _unique(items: Iterable) -> tuple:
    return tuple(dict.fromkeys(items))


def _sum(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) if values else None


def _row(name: str, strategy: str, members: list[Member]) -> Row:
    bots = [m.bot for m in members]
    armed = [b for b in bots if b.enabled]
    contracts = [b.contracts or 0 for b in bots if b.sizing != "dollars"]
    days = [_days(b) for b in bots]
    weekly = all(d is not None for d in days)
    accounts = [(b.broker, b.account_number, b.tag_id) for b in bots]
    times = [m.times for m in members]
    legs = [tuple(bot_summary.legs(b)) for b in bots]
    return Row(
        key="-".join(str(b.id) for b in bots),
        name=name,
        strategy=strategy,
        members=tuple(members),
        underlying=" ".join(_unique(b.underlying for b in bots if b.underlying)),
        armed=bool(armed),
        live=any(b.live for b in armed),
        paper=bool(armed) and not any(b.live for b in armed),
        archived=all(b.archived for b in bots),
        lots=sum(contracts) if contracts else None,
        lots_partial=len(contracts) != len(bots),
        pl=_sum([m.pl for m in members if m.pl is not None and not m.paper_pl]),
        paper_pl=_sum([m.pl for m in members if m.pl is not None and m.paper_pl]),
        pl_shared=len(set(accounts)) != len(accounts),
        weekly=weekly,
        days=tuple(sorted({d for ds in days if ds for d in ds})) if weekly else (),
        days_label=" / ".join(_unique(schedule.summary(b)[0] for b in bots)),
        shared_times=len(set(times)) == 1,
        first_time=min((t for ts in times for t in ts), default="99:99"),
        legs=legs[0],
        legs_differ=len(set(legs)) > 1,
        exits=_unique(" · ".join(bot_summary.exit_lines(b)) or "—" for b in bots),
        tags=_unique(t for b in bots for t in b.tags),
    )


def rows(
    bots: Iterable[BotDef],
    account_name: Callable[[BotDef], str],
    strategy_names: Mapping[int, str],
    pl: Mapping[int, tuple[Decimal, bool]] | None = None,
) -> list[Row]:
    """The list's rows, in Tracker2's order: by strategy, armed first, then the first
    weekday they fire, their first entry time and their name. `pl` is {bot id:
    (its record's closed P/L, whether that is a Paper bot's pretend trades)}."""
    pl = pl or {}
    groups: dict[tuple, tuple[str, str, list[Member]]] = {}
    for bot in bots:
        account = account_name(bot)
        name = base_name(bot.name, account)
        strategy = strategy_names.get(bot.tag_id, NO_STRATEGY) if bot.tag_id else NO_STRATEGY
        amount, paper = pl.get(bot.id, (None, False))
        member = Member(bot, account, _times(bot), amount, paper)
        key = (bot.tag_id, name.casefold())
        groups.setdefault(key, (name, strategy, []))[2].append(member)
    out = [_row(name, strategy, members) for name, strategy, members in groups.values()]
    out.sort(key=lambda r: (r.strategy.casefold(), not r.armed,
                            r.days[0] if r.weekly and r.days and len(r.days) < 5 else 0,
                            r.first_time, r.name.casefold()))  # fmt: skip
    return out


def by_strategy(rows_: list[Row]) -> list[tuple[str, list[Row]]]:
    """The rows under their strategy, alphabetically; rows with nothing armed go last,
    under Off (Tracker2's Disarmed group)."""
    groups: dict[str | None, list[Row]] = {}
    for row in rows_:
        groups.setdefault(row.strategy if row.armed else None, []).append(row)
    out = [(g, groups[g]) for g in sorted((g for g in groups if g is not None), key=str.casefold)]
    if None in groups:
        out.append((OFF_GROUP, groups[None]))
    return out
