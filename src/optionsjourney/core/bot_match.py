"""Which past journal trades look like a bot's (spec: match past trades to bots, #164;
Tracker2's services/strategy_matcher._score_match). Pure: a bot's pattern and a
position in, a score and the reasons out. services/bot_match.py reads the journal and
files the trades the user ticks.

Hard filters (a miss: not a candidate): the bot's broker and account, its underlying
(SPX also matches SPXW), its leg shape (side, call or put, ratio; strikes and DTE
ignored), within WINDOW_MINUTES of one of its entry times when it has any, and on one
of its days when it trades on chosen days. Tracker2's weights: account 1, shape 2,
time 3, day 1. A match is strong (ticked by default) only when the bot has entry
times to check: a shape alone is a guess."""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from functools import reduce

from optionsjourney.core.chain import WEEKLY_ROOTS
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.positions import Position

WINDOW_MINUTES = 2  # Tracker2 is exact on the minute; a bot's entry chases for a while
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

ACCOUNT_WEIGHT, SHAPE_WEIGHT, TIME_WEIGHT, DAY_WEIGHT = 1, 2, 3, 1

# A shape: its legs as sorted (side, right, ratio), the ratios in lowest terms
Shape = tuple[tuple[str, str, int], ...]


@dataclass(frozen=True)
class Pattern:
    """What a bot's trades look like."""

    broker: str
    account_number: str
    underlying: str
    shape: Shape  # the whole bot
    group_shapes: tuple[Shape, ...] = ()  # each side, for a split iron condor's half
    entry_times: tuple[str, ...] = ()  # "HH:MM" or "HH:MM:SS", New York
    days: frozenset[int] = field(default_factory=frozenset)  # weekly: 1 = Monday
    dates: frozenset[date] = field(default_factory=frozenset)  # specific dates


@dataclass(frozen=True)
class Match:
    score: int
    strong: bool
    reasons: tuple[str, ...]  # "same legs", "opened 10:30", "Monday"

    @property
    def reason(self) -> str:
        return " · ".join(self.reasons)


def shape_of(legs: Iterable[tuple[str, str, int]]) -> Shape:
    """(side, right, quantity) legs as a shape: quantities divided by their gcd, legs of
    the same side and right added together, sorted."""
    totals: dict[tuple[str, str], int] = {}
    for side, right, quantity in legs:
        key = (str(side).lower(), right.lower())
        totals[key] = totals.get(key, 0) + int(quantity)
    gcd = reduce(math.gcd, totals.values(), 0) or 1
    return tuple(sorted((side, right, q // gcd) for (side, right), q in totals.items()))


def position_shape(position: Position) -> Shape | None:
    """The shape the trade opened with (its first order's legs), or None when it holds
    more than options."""
    opening = {f.symbol for event in position.events[:1] for o in event.orders for f in o.fills}
    legs = [leg for leg in position.legs if leg.symbol in opening] or position.legs
    if not legs or any(leg.option is None for leg in legs):
        return None
    return shape_of(
        ("sell" if leg.sign < 0 else "buy", leg.option.put_call, leg.opened) for leg in legs
    )


def _root(symbol: str) -> str:
    weekly = {w: root for root, w in WEEKLY_ROOTS.items()}
    symbol = symbol.strip().upper()
    return weekly.get(symbol, symbol)


def _seconds(text: str) -> int:
    parts = [int(p) for p in text.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)


def score(pattern: Pattern, position: Position) -> Match | None:
    """How well the position matches the bot, or None when a hard filter misses."""
    if (position.broker, position.account_number) != (pattern.broker, pattern.account_number):
        return None
    if _root(position.underlying) != _root(pattern.underlying):
        return None
    shape = position_shape(position)
    shapes = (pattern.shape, *(pattern.group_shapes if position.split_from else ()))
    if shape is None or shape not in shapes:
        return None
    total, reasons = ACCOUNT_WEIGHT + SHAPE_WEIGHT, ["same legs"]
    opened = position.opened_at.astimezone(MARKET_TZ)
    if pattern.entry_times:
        at = opened.hour * 3600 + opened.minute * 60 + opened.second
        if not any(abs(at - _seconds(t)) <= WINDOW_MINUTES * 60 for t in pattern.entry_times):
            return None
        total += TIME_WEIGHT
        reasons.append(f"opened {opened:%H:%M}")
    if pattern.days or pattern.dates:
        if opened.isoweekday() not in pattern.days and opened.date() not in pattern.dates:
            return None
        total += DAY_WEIGHT
        reasons.append(DAY_NAMES[opened.weekday()])
    return Match(total, bool(pattern.entry_times), tuple(reasons))


def best[K](scored: Iterable[tuple[K, Match]]) -> list[K]:
    """Match all bots (#169): the keys with the top score, in order. More than one is a
    tie: the trade could be any of them, so it's left for you to choose."""
    scored = list(scored)
    top = max((m.score for _, m in scored), default=None)
    return [k for k, m in scored if m.score == top]
