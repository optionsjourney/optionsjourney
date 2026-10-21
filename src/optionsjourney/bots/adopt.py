"""Matching a trade to a bot (spec: bots slice 12, widened in slice 15; Tracker2's
services/bots/analyzer_shape.py and adopt.py). Pure: which trades and which bots,
how the trade's legs pair with the bot's legs, the rules a held trade must meet, and
the exits a bot's own fill at those prices would get. services/bot_adopt.py (a trade
you opened, handed over) and services/bot_ticket.py (a Trade page ticket sent as a
bot's entry) read the journal, the broker and the quotes.

A bot's legs are a shape: side, call or put, ratio and group. The trade brings the
strikes, expirations, prices and size (analyzer_shape: "Expiry and strike always come
from the tray"). The bot's ratios are in lowest terms; the trade's gcd is its units."""

import hashlib
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import reduce

from optionsjourney.bots import exit_math, exit_plan, futures, stops, strikes, underlyings
from optionsjourney.bots.definition import (
    MAX_DTE,
    MAX_LEGS,
    BotDef,
    LegDef,
    holds_overnight,
    largest_ratio,
    limits,
)
from optionsjourney.trading.orders import Side

WHICH_TRADES = (
    f"Only an options trade of up to {MAX_LEGS} legs, opened today, with nothing closed, can "
    "be handed to a bot."
)


class Refused(ValueError):
    """Why a trade can't go to a bot (Tracker2's AdoptRefused, ShapeMismatch)."""


@dataclass(frozen=True)
class HeldLeg:
    """One leg of the trade: its option, direction, size and price (a journal trade's
    fill; 0 for a ticket's leg, whose prices are read when it's sent)."""

    symbol: str
    right: str  # "call" or "put"
    strike: Decimal
    expiration: date
    side: Side  # SELL: a short
    quantity: int  # contracts
    price: Decimal  # per contract in option price


Pairs = list[tuple[HeldLeg, LegDef]]


@dataclass(frozen=True)
class Matched:
    """The trade as a filled run would hold it (adopt._record's frozen basis)."""

    legs: list[dict]  # the run's legs, in the bot's leg order
    filled_legs: list[dict]
    credit: Decimal  # the whole trade's price per unit, + a credit
    contracts: int  # units


@dataclass(frozen=True)
class SidePlan:
    """One group's exits as place_protections would set them."""

    position: int
    name: str
    short: str  # the leg a resting stop is on; "" when the app watches the group
    long: str  # the first other leg, or ""
    contracts: int  # group units
    stop: stops.StopCalc | None  # None: the bot sets no stop on this group
    target: Decimal | None  # the value to close at; None: no target
    target_problem: str | None = None
    legs: tuple[exit_math.GroupLeg, ...] = ()
    watched: bool = False  # the stop is watched by the app on the group's P&L
    entry: Decimal | None = None  # the group's entry per unit
    rests: str | None = None  # decision 0048: "stop", "target" or None at the broker
    notes: tuple[str, ...] = ()  # what the fill drops or ignores (exit_plan.read_against_fill)
    app_held: bool = False  # the stop is held by the app on its short (decision 0048)


def _num(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _gcd(values) -> int:
    return reduce(math.gcd, (int(v) for v in values), 0)


def describe(legs: list[HeldLeg]) -> str:
    """'1 short put + 1 long put' (analyzer_shape.describe)."""
    ordered = sorted(legs, key=lambda leg: (leg.right != "put", leg.strike))
    words = [
        f"{leg.quantity} {'short' if leg.side is Side.SELL else 'long'} {leg.right}"
        for leg in ordered
    ]
    return " + ".join(words) or "No legs"


def trade_problem(legs: list[HeldLeg], opened_on: date, today: date) -> str | None:
    """Why this trade can't be handed to any bot, or None (adopt.check_position). The
    shape and the expirations are each bot's to check (bot_problem)."""
    if not legs or len(legs) > MAX_LEGS or any(leg.right not in ("call", "put") for leg in legs):
        return f"It isn't an options trade of up to {MAX_LEGS} legs. {WHICH_TRADES}"
    if any(leg.quantity < 1 for leg in legs):
        return "Its contracts can't be read."
    if opened_on != today:
        return f"It wasn't opened today. {WHICH_TRADES}"
    return None


def pair(bot: BotDef, legs: list[HeldLeg]) -> tuple[int, Pairs]:
    """(units, [(trade leg, bot leg)]) (analyzer_shape.match): each bot leg, in position
    order, takes the remaining trade leg of its side, type and ratio (the trade's
    contracts divided by their gcd) with the lowest strike; a strike tie goes by
    expiration in the order of the bot legs' DTEs, so a calendar pairs the same way
    every time. Like Tracker2, a ticket that could be split into the bot's groups more
    than one way takes this pairing: the check and the picker show it. Raises Refused."""
    if not legs or len(legs) != len(bot.legs):
        raise Refused(f"{describe(legs)} doesn't match a {len(bot.legs)}-leg bot.")
    if _gcd(leg.ratio for leg in bot.legs) != 1:
        raise Refused("Its leg ratios aren't in lowest terms: set them as small as they go.")
    units = _gcd(leg.quantity for leg in legs)
    if units < 1:
        raise Refused("The trade's contracts can't be read.")
    bot_legs = sorted(bot.legs, key=lambda b: b.position)
    classes: dict[tuple, list[int]] = {}
    for t, leg in enumerate(legs):
        classes.setdefault((leg.side, leg.right, leg.quantity // units), []).append(t)
    pairs = []
    for key in {(b.side, b.right, b.ratio) for b in bot_legs}:
        mine = [b for b in bot_legs if (b.side, b.right, b.ratio) == key]
        trade_ids = classes.pop(key, [])
        if len(trade_ids) != len(mine):
            raise Refused(f"{describe(legs)} doesn't match its legs.")
        pairs.extend(zip(_in_order(legs, trade_ids, mine), mine, strict=True))
    if classes:
        raise Refused(f"{describe(legs)} doesn't match its legs.")
    return units, sorted(pairs, key=lambda p: p[1].position)


def _in_order(legs: list[HeldLeg], trade_ids: list[int], mine: list[LegDef]) -> list[HeldLeg]:
    """The trade legs of one shape in one group, ordered to pair with `mine` (the bot's
    legs of that shape, by position): by strike, and a run of equal strikes by
    expiration, the earliest to the bot leg with the smallest DTE."""
    ordered = sorted((legs[t] for t in trade_ids), key=lambda leg: leg.strike)
    out = list(ordered)
    start = 0
    while start < len(ordered):
        end = start
        while end < len(ordered) and ordered[end].strike == ordered[start].strike:
            end += 1
        block = sorted(ordered[start:end], key=lambda leg: leg.expiration)
        slots = sorted(range(start, end), key=lambda k: (mine[k].dte, mine[k].position))
        for leg, slot in zip(block, slots, strict=True):
            out[slot] = leg
        start = end
    return out


def _root(symbol: str) -> str:
    return symbol[:6].strip()  # trading.orders.Leg.root for an OCC symbol


def held_problem(bot: BotDef, pairs: Pairs, today: date) -> str | None:
    """Slice 14's rules read on the trade's own expirations (spec question 7), or None."""
    for held, _ in pairs:
        if held.expiration < today:
            return f"{held.symbol.strip()} has expired."
        if (held.expiration - today).days > MAX_DTE:
            return f"{held.symbol.strip()} is more than {MAX_DTE} days from expiring."
    if underlyings.futures(bot.underlying):
        return _futures_problem(bot, pairs)
    root = underlyings.root(bot.underlying)
    for held, _ in pairs:
        if _root(held.symbol) != root:
            # strikes._weekly_only: a monthly SPX option stops trading the day before; an
            # adjusted contract (SPY1) isn't 100 shares (slice 16)
            if bot.underlying.upper() == "SPX":
                return (
                    f"{held.symbol.strip()} is a monthly {bot.underlying} option (AM-settled); "
                    f"bots trade {root} only."
                )
            return strikes.off_root(held.symbol, root, bot.underlying)
    held_over = any(held.expiration > today for held, _ in pairs)
    return _held_over_problem(bot, pairs, held_over)


def _futures_problem(bot: BotDef, pairs: Pairs) -> str | None:
    """Bots slice 17: every leg a futures option on a month of the bot's root (in place
    of the weekly root), the same expiration rules within a group, and no same-day rule:
    a futures bot holds its trade until its target fills or it expires."""
    for held, _ in pairs:
        contract = futures.contract_of(held.symbol)
        if contract is None:
            return f"{held.symbol.strip()} isn't a {bot.underlying} futures option."
        problem = futures.contract_problem(bot.underlying, contract)
        if problem:
            return problem
    return _group_problem(bot, pairs)


def _held_over_problem(bot: BotDef, pairs: Pairs, held_over: bool) -> str | None:
    if held_over and not holds_overnight(bot):
        return (
            "This trade is held past today and the bot is a same-day bot. Use a bot with a leg "
            "above 0 DTE."
        )
    return _group_problem(bot, pairs)


def _group_problem(bot: BotDef, pairs: Pairs) -> str | None:
    for group in bot.groups:
        own = [(held, b) for held, b in pairs if b.group == group.position]
        bought = [held.expiration for held, b in own if b.side is Side.BUY]
        if bought and any(h.expiration > min(bought) for h, b in own if b.side is Side.SELL):
            # validation._expiries, on the trade's dates
            return (
                f"In {group.name} a sold leg expires after a bought leg (a reverse calendar): a "
                "bot never holds that."
            )
        if len({held.expiration for held, _ in own}) > 1 and bot.close_time is None:
            # validation._timed_close's rule for a calendar or diagonal
            return (
                f"{group.name} is in more than one expiration, and a calendar or diagonal must "
                "close before its near legs expire: the bot needs a timed close."
            )
    return None


def picked_of(pairs: Pairs, prices) -> tuple[strikes.Picked, ...]:
    """The pairs as an entry's picked legs, in the bot's leg order, each at its price
    (`prices`: symbol -> price per contract)."""
    return tuple(
        strikes.Picked(b.position, held.symbol, held.strike, held.right, held.side, b.ratio,
                       b.group, prices[held.symbol])
        for held, b in sorted(pairs, key=lambda p: p[1].position)
    )  # fmt: skip


def priced_problem(bot: BotDef, pairs: Pairs, prices) -> str | None:
    """The trade not too close to even, either side (decision 0048; bot_entry's check,
    strikes.too_close_to_even), and inside a credit trade no group with one sold leg
    pricing as a debit (strikes.inverted_group). `prices`: symbol -> price per contract."""
    picked = picked_of(pairs, prices)
    problem = strikes.too_close_to_even(strikes.entry_price(picked, Decimal(0)))
    if problem is not None:
        return problem
    try:
        strikes.inverted_group(bot, picked)
    except strikes.Unsafe as e:
        return str(e)
    return None


def bot_problem(bot: BotDef, legs: list[HeldLeg], underlying: str, *, today: date) -> str | None:
    """Why this bot can't take this trade, or None (adopt.check_bot, shape_for)."""
    if bot.archived:
        return "It's archived."
    if not bot.live:
        return "It's a Paper bot, so it would place nothing."
    if underlyings.futures(bot.underlying) and bot.broker != "tastytrade":
        return "Futures options trade through TastyTrade only."  # FUTURES_TT_ONLY
    if bot.underlying.upper() != underlying.upper():
        return f"It trades {bot.underlying}, not {underlying}."
    try:
        units, pairs = pair(bot, legs)
    except Refused as e:
        return str(e)
    contracts = units * largest_ratio(bot)
    per_trade, _ = limits(bot, by_hand=True)  # decision 0058: a trade you hand it
    if contracts > per_trade:
        # run_analyzer_entry: the caps are a hard limit (refuse, don't clamp)
        return (
            f"The trade's {contracts} contracts in its largest leg are over its limit of "
            f"{per_trade} per trade."
        )
    return held_problem(bot, pairs, today)


def fits(bot: BotDef, legs: list[HeldLeg], underlying: str, *, today: date) -> str | None:
    """bot_problem, then priced_problem at the legs' own prices: why a bot can't take a
    trade whose prices are known (a journal trade's fills), or None."""
    problem = bot_problem(bot, legs, underlying, today=today)
    if problem is not None:
        return problem
    _, pairs = pair(bot, legs)
    return priced_problem(bot, pairs, {held.symbol: held.price for held, _ in pairs})


def match(bot: BotDef, legs: list[HeldLeg], *, today: date) -> Matched:
    """The trade as the bot's filled run, at the legs' own prices (analyzer_shape.
    build_trade). Raises Refused."""
    problem = fits(bot, legs, bot.underlying, today=today)
    if problem is not None:
        raise Refused(problem)
    units, pairs = pair(bot, legs)
    prices = {held.symbol: held.price for held, _ in pairs}
    run_legs, filled = [], []
    for held, bot_leg in sorted(pairs, key=lambda p: p[1].position):
        run_legs.append(
            {
                "position": bot_leg.position,
                "symbol": held.symbol,
                "strike": _num(held.strike),
                "right": held.right,
                "side": str(held.side),
                "ratio": bot_leg.ratio,
                "group": bot_leg.group,
                "mid": f"{held.price:f}",
                "expiration": held.expiration.isoformat(),
                "dte": (held.expiration - today).days,
            }
        )
        filled.append(
            {
                "symbol": held.symbol,
                "side": str(held.side),
                "fill_price": f"{held.price:f}",
                "contracts": units * bot_leg.ratio,
            }
        )
    credit = strikes.entry_price(picked_of(pairs, prices), Decimal(0))
    return Matched(run_legs, filled, credit, units)


def plan_exits(bot: BotDef, matched: Matched) -> list[SidePlan]:
    """Each group's stop and target exactly as bot_exits.place_protections works them out
    for a fill at these prices (exits.place_group_protections), read against the side it
    filled on (decision 0048, exit_plan.plan_group): what rests at the broker, what the app
    watches, and what the fill drops. A futures bot rests its target only (decision 0043)."""
    fills = {f["symbol"]: Decimal(f["fill_price"]) for f in matched.filled_legs}
    credit = matched.credit > 0
    names = {g.position: g.name for g in bot.groups}
    is_futures = underlyings.futures(bot.underlying)
    out = []
    for group in sorted(bot.groups, key=lambda g: g.position):
        legs, gcd = exit_math.group_legs(matched.legs, group.position)
        plan = exit_plan.plan_group(group, (leg.side for leg in legs), credit, bot.broker,
                                    bot.stop_co_mode)  # fmt: skip
        plan = exit_plan.with_pairing(plan, group, names)
        anchor = exit_math.anchor_of(legs) if plan.on_short else None
        other = next((leg for leg in legs if leg != anchor), None)
        entry = exit_math.group_entry(legs, fills)
        if is_futures:  # bots slice 17: a futures run places its profit target only
            calc, rests = None, "target"
            limit, problem = stops.target_limit(group, entry) if entry is not None else (None, None)
        else:
            calc, rests = exit_plan.stop_for(plan, legs, fills, matched.credit, bot.stop_co_mode,
                                             bot.stop_co_amount), plan.rests  # fmt: skip
            limit, problem = exit_plan.target_for(plan, legs, fills)
        out.append(SidePlan(
            group.position, group.name, anchor.symbol if anchor else "",
            other.symbol if other else "", matched.contracts * gcd, calc, limit, problem,
            tuple(legs), anchor is None, entry, rests, plan.notes,
            app_held=calc is not None and plan.stop_on == "short",
        ))  # fmt: skip
    return out


def _amount(unit: str, pct: Decimal | None, usd: Decimal | None) -> str | None:
    if unit == "usd":
        return None if usd is None else f"${_num(usd)}"
    return None if pct is None else f"{_num(pct)}%"


def exit_summary(bot: BotDef) -> list[str]:
    """One line per group of what it sets (analyzer_shape.exit_summary)."""
    lines = []
    for group in sorted(bot.groups, key=lambda g: g.position):
        stop = _amount(group.stop_unit, group.stop_pct, group.stop_usd)
        target = _amount(group.target_unit, group.target_pct, group.target_usd)
        if underlyings.futures(bot.underlying):  # "PT 50% · no stop (futures)"
            lines.append(
                f"{group.name}: {'target ' + target if target else 'no target'} · no stop (futures)"
            )
            continue
        lines.append(
            f"{group.name}: {'stop ' + stop if stop else 'no stop'} · "
            f"{'target ' + target if target else 'no target'}"
        )
    return lines


def _leg_words(held: HeldLeg, ratio: int) -> str:
    size = f"{ratio}x " if ratio > 1 else ""
    side = "sell" if held.side is Side.SELL else "buy"
    return f"{side} {size}{_num(held.strike)}{held.right[0].upper()} {held.expiration:%b %d}"


def group_lines(bot: BotDef, pairs: Pairs) -> list[str]:
    """The pairing the bot will use, one line per group with its exits: "Put side: sell
    5700P Oct 13 / buy 5625P Oct 13 · stop 95% · no target" (the picker shows it before
    you send)."""
    exits = dict(zip((g.position for g in sorted(bot.groups, key=lambda g: g.position)),
                     exit_summary(bot), strict=True))  # fmt: skip
    lines = []
    for group in sorted(bot.groups, key=lambda g: g.position):
        own = [(h, b) for h, b in pairs if b.group == group.position]
        legs = " / ".join(_leg_words(h, b.ratio) for h, b in own)
        lines.append(f"{group.name}: {legs} · {exits[group.position].split(': ', 1)[1]}")
    return lines


def fingerprint(bot_id: int, matched: Matched, sides: list[SidePlan]) -> str:
    """What the check showed, in short: confirming refuses if it changed."""
    parts = [str(bot_id), str(matched.contracts), f"{matched.credit:f}"]
    for side in sides:
        trigger = side.stop.trigger if side.stop is not None else None
        legs = ",".join(leg.symbol for leg in side.legs)
        parts.append(f"{side.position}:{side.short}:{side.long}:{legs}:{trigger}:{side.target}"
                     f":{side.rests}")  # fmt: skip
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
