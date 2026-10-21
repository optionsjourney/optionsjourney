"""Picking a bot's strikes off the live chain, and its entry price (spec:
the first automation, section 2; Tracker2's resolver.py and selection.py).
Pure.

Premium legs pick the strike whose mid is nearest the target among
out-of-the-money strikes on their own side (calls above spot, puts below);
ties go further out. This tightens Tracker2's select_by_premium, which looks
on both sides and leans on a sanity check. Offset legs sit a number of strike
points from their linked leg: exactly, or the next strike further out.

Bots slice 10: delta legs (selection.select_by_delta) take the out-of-the-money
strike whose |delta| is nearest the target, the same tightening, and fail closed
when the chain's deltas are missing; spot legs (select_by_spot_offset) take the
listed strike nearest the price plus their points."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from optionsjourney.bots import exit_math
from optionsjourney.bots.definition import BotDef, LegDef
from optionsjourney.core import black_scholes
from optionsjourney.core.symbols import parse_occ
from optionsjourney.trading.orders import Side, plain

MIN_CREDIT = Decimal("0.05")
# A delta leg needs a delta on this share of its side's priced strikes (Tracker2's
# chain.DXLINK_MIN_COVERAGE): below it the right strike may just not have reported.
DELTA_COVERAGE = Decimal("0.95")
# A streamed Greeks delta is used while it's this new: Greeks are recomputed as the
# underlying moves. Older, the delta is worked out from the mid instead. The quote's own
# age doesn't matter while the stream is healthy: DXLink sends on change, so a quiet
# far-out 0DTE quote can be minutes old and still current.
GREEK_FRESH = timedelta(seconds=120)
# A short this deep is the sign of a stale chain or an unreachable target
# (resolver._sanity_check's |delta| >= 0.90).
STALE_SHORT_DELTA = Decimal("0.90")


class NoTrade(Exception):
    """The chain doesn't allow this trade now. The message says why."""


class NoData(NoTrade):
    """The market data a pick needs (deltas, a fresh price) is missing or old: the
    entry is skipped and the bot alerts, never falling back to another pick. `kind`
    names it for the alert ("deltas", "SPX price")."""

    def __init__(self, message: str, kind: str = "deltas"):
        super().__init__(message)
        self.kind = kind


class Unsafe(NoTrade):
    """The picked legs fail the sanity check (an in-the-money short, an
    inverted side, the same option twice): a mistake in the setup or the
    chain, so the run is an error, not just a skip."""


@dataclass(frozen=True)
class Quote2:
    symbol: str
    bid: Decimal | None
    ask: Decimal | None
    delta: Decimal | None = None  # signed, read only for a bot with a delta leg

    @property
    def mid(self) -> Decimal | None:
        if self.bid is None or self.ask is None or self.ask < self.bid or self.ask <= 0:
            return None
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Chain:
    spot: Decimal
    quotes: Mapping[tuple[Decimal, str], Quote2]  # (strike, "call"/"put") -> quote


@dataclass(frozen=True)
class Picked:
    position: int
    symbol: str
    strike: Decimal
    right: str
    side: Side
    ratio: int
    group: int
    mid: Decimal
    delta: Decimal | None = None

    @property
    def expiration(self) -> date | None:
        """The option's expiration, from its OCC symbol (bots slice 14)."""
        parsed = parse_occ(self.symbol)
        return parsed[1].expiration if parsed else None


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def chain_from_rows(
    rows: Mapping,
    spot: float,
    *,
    deltas: bool = False,
    now: datetime | None = None,
    expires_at: datetime | None = None,
    stream_ok: bool = False,
) -> Chain:
    """From the analyzer's rows: {strike: {"call": {"bid", "ask", "symbol"}, "put": …}}.
    With `deltas` (a bot with a delta leg), each option's delta. A row with no "at" is
    Schwab's snapshot, taken for this entry: its own delta when it sends a real one,
    else one worked out from the mid (`model_delta`). A streamed row (with "at") counts
    only while `stream_ok` (the stream is up and the underlying is updating): the
    stream's Greeks first (chain.fetch_chain reads DXLink's first), else the model."""
    quotes = {}
    for strike, row in rows.items():
        for right in ("call", "put"):
            side = row.get(right) or {}
            if side.get("symbol"):
                quote = Quote2(side["symbol"], _dec(side.get("bid")), _dec(side.get("ask")))
                if deltas:
                    found = _row_delta(side, quote, spot, strike, right, now, expires_at,
                                       stream_ok)  # fmt: skip
                    quote = Quote2(quote.symbol, quote.bid, quote.ask, found)
                quotes[(_dec(strike), right)] = quote
    return Chain(_dec(spot), quotes)


def _sent_delta(value) -> Decimal | None:
    """A broker's delta, if it's a real one: Schwab sends -999 (or NaN) for none."""
    try:
        found = Decimal(str(value))
    except InvalidOperation, ValueError, TypeError:
        return None
    if not found.is_finite() or not -1 <= found <= 1:
        return None
    return found


def _row_delta(side, quote, spot, strike, right, now, expires_at, stream_ok) -> Decimal | None:
    at = side.get("at")
    if at is None:  # Schwab's snapshot
        found = _sent_delta(side.get("delta"))
        return (
            found
            if found is not None
            else model_delta(quote.mid, spot, strike, right, now, expires_at)
        )
    if not stream_ok or now is None:
        return None  # the stream is down or the underlying isn't updating: nothing is current
    greek_at = side.get("stream_delta_at")
    found = _sent_delta(side.get("stream_delta"))
    if (
        found is not None
        and isinstance(greek_at, datetime)
        and timedelta(0) <= now - greek_at <= GREEK_FRESH
    ):
        return found
    return model_delta(quote.mid, spot, strike, right, now, expires_at)


def model_delta(mid, spot, strike, right: str, now, expires_at) -> Decimal | None:
    """The delta from the mid's implied volatility (Black-Scholes at 4.5%, to the
    16:00 close; analyzer._table_side), or None when no volatility gives that price."""
    if mid is None or not spot or now is None or expires_at is None:
        return None
    years = max((expires_at - now).total_seconds(), 60) / black_scholes.YEAR
    kind = right.upper()
    iv = black_scholes.implied_vol(
        float(mid), float(spot), float(strike), years, black_scholes.RATE, kind
    )
    if not iv:
        return None
    delta = black_scholes.greeks(float(spot), float(strike), years, black_scholes.RATE, iv, kind)
    if not math.isfinite(delta["delta"]):
        return None
    return Decimal(str(round(delta["delta"], 4)))


def _out_of_the_money(strike: Decimal, right: str, spot: Decimal) -> bool:
    return strike > spot if right == "call" else strike < spot


def _further_out(strike: Decimal, right: str) -> Decimal:
    return strike if right == "call" else -strike  # bigger is further out


def _pool(leg: LegDef, chain: Chain) -> list[tuple[Decimal, Quote2]]:
    """The priced strikes on the leg's right it may land on: a sold leg only out of the
    money; a bought leg anywhere (a long call can be in the money)."""
    return [
        (strike, quote)
        for (strike, right), quote in chain.quotes.items()
        if right == leg.right
        and quote.mid is not None
        and (leg.side is not Side.SELL or _out_of_the_money(strike, right, chain.spot))
    ]


def _by_premium(leg: LegDef, chain: Chain) -> tuple[Decimal, Quote2]:
    candidates = _pool(leg, chain)
    if not candidates:
        where = "out-of-the-money " if leg.side is Side.SELL else ""
        raise NoTrade(f"No {where}{leg.right} with a price.")
    return min(
        candidates, key=lambda c: (abs(c[1].mid - leg.target), -_further_out(c[0], leg.right))
    )


def _magnitude(text: Decimal) -> str:
    return f"{text.normalize():f}"


def _by_delta(leg: LegDef, chain: Chain) -> tuple[Decimal, Quote2]:
    """select_by_delta over the leg's pool (a sold leg's out-of-the-money strikes, a
    bought leg's every strike on its side; see _pool); nearest
    |delta|, ties further out. Too few deltas is NoData (chain_has_deltas, the 95%
    coverage); a target further out than any listed delta skips."""
    priced = _pool(leg, chain)
    if not priced:
        where = "out-of-the-money " if leg.side is Side.SELL else ""
        raise NoTrade(f"No {where}{leg.right} with a price.")
    known = [(strike, quote) for strike, quote in priced if quote.delta is not None]
    # A call's delta is 0 or more and a put's 0 or less: the other sign is bad data
    wrong = [k for k, q in known if (q.delta < 0 if leg.right == "call" else q.delta > 0)]
    if wrong:
        raise NoData(
            f"Deltas for the {leg.right}s have the wrong sign (at {wrong[0]:f}); "
            "the chain can't be trusted."
        )
    if len(known) < DELTA_COVERAGE * len(priced):
        raise NoData(
            f"Deltas unavailable for the {leg.right}s: {len(known)} of {len(priced)} "
            "strikes have one."
        )
    target = abs(leg.target)
    if not any(abs(quote.delta) <= target for _, quote in known):
        furthest = min(abs(quote.delta) for _, quote in known)
        raise NoTrade(
            f"No {leg.right} as far out as {_magnitude(target)} delta; "
            f"the furthest is {_magnitude(furthest)}."
        )
    return min(
        known,
        key=lambda c: (abs(abs(c[1].delta) - target), -_further_out(c[0], leg.right)),
    )


def _by_spot(leg: LegDef, chain: Chain) -> tuple[Decimal, Quote2]:
    """select_by_spot_offset: the listed strike nearest the price plus the points,
    a tie going away from the money. A short rounds only outward (further out of the
    money than the points), so it can never land at or through the money. At 0 points
    (an iron fly or straddle's body) it is the nearest strike on any side, a tie going
    to the lower strike, so a call and a put land on one strike."""
    wanted = chain.spot + leg.target
    if leg.target == 0:
        near = [
            (strike, quote)
            for (strike, right), quote in chain.quotes.items()
            if right == leg.right and quote.mid is not None
        ]
        if not near:
            raise NoTrade(f"No {leg.right} with a price.")
        return min(near, key=lambda c: (abs(c[0] - wanted), c[0]))
    listed = [
        (strike, quote)
        for (strike, right), quote in chain.quotes.items()
        if right == leg.right
        and quote.mid is not None
        and (
            leg.side is not Side.SELL or _further_out(strike, right) >= _further_out(wanted, right)
        )
    ]
    if not listed:
        raise NoTrade(f"No {leg.right} with a price.")
    return min(listed, key=lambda c: (abs(c[0] - wanted), -_further_out(c[0], leg.right)))


_PICKERS = {"premium": _by_premium, "delta": _by_delta, "spot": _by_spot}


def _by_offset(leg: LegDef, base: Decimal, chain: Chain) -> tuple[Decimal, Quote2]:
    wanted = base + leg.target
    if leg.exact:
        quote = chain.quotes.get((wanted, leg.right))
        if quote is None or quote.mid is None:
            raise NoTrade(f"No {leg.right} at {wanted:f} to buy (exact strike).")
        return wanted, quote
    listed = sorted(
        (strike, quote) for (strike, right), quote in chain.quotes.items()
        if right == leg.right and quote.mid is not None
    )  # fmt: skip
    if leg.right == "call":
        found = [c for c in listed if c[0] >= wanted]
        pick = found[0] if found else None
    else:
        found = [c for c in listed if c[0] <= wanted]
        pick = found[-1] if found else None
    if pick is None:
        raise NoTrade(f"No {leg.right} at or beyond {wanted:f}.")
    return pick


def _by_linked(leg: LegDef, base: Decimal, chain: Chain, said: list) -> tuple[Decimal, Quote2]:
    """Slice 19 (selection.select_linked_leg): a bought premium wing takes the priced
    strike nearest its target strictly beyond its short and no further than its max
    offset (puts below, calls above), a tie going further out. With nothing priced
    inside, the priced strike beyond the short nearest the bound, as Tracker2 does
    (question 12, the controller's ruling); none at all: NoTrade."""
    bound = base + leg.max_offset
    beyond = [
        (strike, quote) for (strike, right), quote in chain.quotes.items()
        if right == leg.right and quote.mid is not None
        and (strike > base if leg.right == "call" else strike < base)
    ]  # fmt: skip
    inside = [c for c in beyond if (c[0] <= bound if leg.right == "call" else c[0] >= bound)]
    if inside:
        return min(
            inside, key=lambda c: (abs(c[1].mid - leg.target), -_further_out(c[0], leg.right))
        )
    if not beyond:
        where = "above" if leg.right == "call" else "below"
        raise NoTrade(f"No {leg.right} priced {where} {base.normalize():f}.")
    found = min(beyond, key=lambda c: (abs(c[0] - bound), c[0]))
    said.append(  # resolve_trade's "clamped to its offset bound" warning, with the strikes
        f"leg {leg.position + 1}: no {leg.right} priced within "
        f"{abs(leg.max_offset).normalize():f} points of {base.normalize():f}; clamped to its "
        f"offset bound, using {found[0].normalize():f} (bound {bound.normalize():f})"
    )
    return found


def _miss(leg: LegDef, quote: Quote2) -> Decimal:
    """How far a strike is from the leg's own target (_enforce_min_short_width._miss):
    a delta leg's |delta|, a premium leg's mid; a spot leg gives up the least premium."""
    if leg.pick == "delta" and quote.delta is not None:
        return abs(abs(quote.delta) - abs(leg.target))
    if leg.pick == "premium":
        return abs(quote.mid - leg.target)
    return -quote.mid


def _lone_short(legs: list[LegDef], right: str) -> LegDef | None:
    found = [leg for leg in legs if leg.side is Side.SELL and leg.right == right]
    return found[0] if len(found) == 1 else None


def _widen(own: list[LegDef], picked: dict, chains, width: Decimal, said: list) -> None:
    """Slice 19 (resolver._enforce_min_short_width): with one short put and one short
    call in one expiration, step them outward one priced strike at a time, whichever
    side's next strike misses its own target less (a tie moves the call), until the
    call's strike less the put's is at least `width`. No strikes left: NoTrade."""
    put, call = _lone_short(own, "put"), _lone_short(own, "call")
    if put is None or call is None or put.dte != call.dte:
        return
    start_put, start_call = picked[put.position].strike, picked[call.position].strike
    if start_call - start_put >= width:
        return
    chain = _chain_for(put, chains)
    priced = [(k, r, q) for (k, r), q in chain.quotes.items() if q.mid is not None]
    puts = sorted(((k, q) for k, r, q in priced if r == "put"), key=lambda c: -c[0])
    calls = sorted(((k, q) for k, r, q in priced if r == "call"), key=lambda c: c[0])
    at_put = (start_put, None)
    at_call = (start_call, None)
    while at_call[0] - at_put[0] < width:
        next_put = next((c for c in puts if c[0] < at_put[0]), None)
        next_call = next((c for c in calls if c[0] > at_call[0]), None)
        if next_put is None and next_call is None:
            raise NoTrade(
                f"Min short width {width.normalize():f}: no strikes left to widen "
                f"{start_put.normalize():f}/{start_call.normalize():f}."
            )
        if next_call is None or (
            next_put is not None and _miss(put, next_put[1]) < _miss(call, next_call[1])
        ):
            at_put = next_put
        else:
            at_call = next_call
    for leg, start, (strike, quote) in ((put, start_put, at_put), (call, start_call, at_call)):
        if quote is None or strike == start:
            continue
        picked[leg.position] = _picked(leg, strike, quote)
        said.append(
            f"short put {start_put.normalize():f} / call {start_call.normalize():f} were "
            f"{(start_call - start_put).normalize():f} apart; the {leg.right} moved to "
            f"{strike.normalize():f} for the {width.normalize():f}-point min short width"
        )


def _nets(p: Picked, held: Mapping[str, int]) -> bool:
    """selection._nets_against_holding: buying where short, or selling where long, would
    close against a position already held (TastyTrade refuses the whole entry then)."""
    quantity = held.get(plain(p.symbol), 0)
    return quantity < 0 if p.side is Side.BUY else quantity > 0


def _unconflict(order: list[LegDef], picked: dict, chains, held, said: list) -> None:
    """Slice 19 (selection.resolve_conflicts): in pick order, a leg held the other way
    or on an option this order already uses moves one listed strike out (away from the
    money). Never further: onto a strike that's taken, held the other way or unpriced it
    doesn't move, and the broker (or the sanity check) is the backstop."""
    seen: set[str] = set()
    for leg in order:
        p = picked[leg.position]
        if _nets(p, held):
            p = _step_out(leg, p, chains, seen, held, "is held the other way", said)
        if p.symbol in seen:
            p = _step_out(leg, p, chains, seen, held, "is already used", said)
        picked[leg.position] = p
        seen.add(p.symbol)


def _step_out(leg: LegDef, p: Picked, chains, seen: set, held, why: str, said: list) -> Picked:
    chain = _chain_for(leg, chains)
    listed = sorted(k for (k, r) in chain.quotes if r == leg.right)
    further = [k for k in listed if (k > p.strike if leg.right == "call" else k < p.strike)]
    if not further:
        return p
    strike = further[0] if leg.right == "call" else further[-1]
    quote = chain.quotes[(strike, leg.right)]
    moved = _picked(leg, strike, quote)
    if quote.mid is None or moved.symbol in seen or _nets(moved, held):
        return p
    said.append(f"strike {p.strike.normalize():f} {leg.right} {why}; moved to "
                f"{strike.normalize():f}")  # fmt: skip
    return moved


def _chain_for(leg: LegDef, chains: Chain | Mapping[int, Chain]) -> Chain:
    """The leg's own expiration's chain (resolver._resolve_one reads chains[leg.dte]); a
    single Chain serves every leg, as before slice 14."""
    if isinstance(chains, Chain):
        return chains
    found = chains.get(leg.dte)
    if found is None:
        raise NoTrade(f"No option chain for {leg.dte} DTE.")
    return found


def _linked(leg: LegDef) -> bool:
    """Placed from another leg's strike (resolver.resolve_trade's pass 2): an offset, or
    from slice 19 a premium wing with a max offset from its short."""
    return leg.pick == "offset" or (leg.pick == "premium" and leg.max_offset is not None)


def _picked(leg: LegDef, strike: Decimal, quote: Quote2) -> Picked:
    return Picked(leg.position, quote.symbol, strike, leg.right, leg.side, leg.ratio,
                  leg.group, quote.mid, quote.delta)  # fmt: skip


def pick_legs(
    legs: tuple[LegDef, ...],
    chains: Chain | Mapping[int, Chain],
    root: str | None = None,
    underlying: str = "SPX",
    *,
    min_width: Decimal | None = None,
    held: Mapping[str, int] | None = None,
    move_conflicted: bool = False,
    notes: list | None = None,
) -> tuple[Picked, ...]:
    """Every leg's strike: each on its own DTE's chain (bots slice 14), offsets from
    their linked leg's strike looked up on their own chain. With `root` (the root the
    bot trades: SPXW for SPX, the symbol itself for the others; underlyings.root), a leg
    on any other root is Unsafe (slice 14 question 11, slice 16).

    Slice 19 (resolver.resolve_trade): the legs picked on their own first; then
    `min_width` widens the shorts (_widen); then the linked legs (offsets, and premium
    wings with a max offset, _by_linked) from where those landed; then, with
    `move_conflicted`, a leg held the other way (`held`: plain symbol -> signed
    contracts) or on an option another leg uses moves one strike out (_unconflict).
    Each move is said in `notes`."""
    picked: dict[int, Picked] = {}
    said = notes if notes is not None else []
    own = [leg for leg in legs if not _linked(leg)]
    for leg in own:
        strike, quote = _PICKERS[leg.pick](leg, _chain_for(leg, chains))
        picked[leg.position] = _picked(leg, strike, quote)
    if min_width is not None:
        _widen(own, picked, chains, min_width, said)
    for leg in legs:
        if not _linked(leg):
            continue
        chain = _chain_for(leg, chains)
        if leg.link not in picked:  # validation refuses a link to another linked leg
            raise Unsafe(f"Leg {leg.position + 1} is measured from a leg that isn't picked.")
        base = picked[leg.link].strike
        if leg.pick == "offset":
            strike, quote = _by_offset(leg, base, chain)
        else:
            strike, quote = _by_linked(leg, base, chain, said)
        picked[leg.position] = _picked(leg, strike, quote)
    if move_conflicted:
        order = own + [leg for leg in legs if _linked(leg)]
        _unconflict(order, picked, chains, held or {}, said)
    result = tuple(picked[leg.position] for leg in legs)
    if root is not None:
        _weekly_only(result, root, underlying)
    spot = chains.spot if isinstance(chains, Chain) else next(iter(chains.values())).spot
    _sanity(result, spot, placed=frozenset(leg.position for leg in legs if offset_short(leg)))
    return result


# Picks that place a strike at a configured distance (from the price, or from another
# leg) rather than searching for a premium or delta target (resolver._OFFSET_MODES).
OFFSET_PICKS = ("spot", "offset")


def offset_short(leg: LegDef) -> bool:
    """A sold leg placed by an offset: it may sit in the money (#206, decision 0082), so
    the entry reads the chain's deltas for it (bot_entry._pick) to check it isn't stale."""
    return leg.side is Side.SELL and leg.pick in OFFSET_PICKS


def _weekly_only(picked: tuple[Picked, ...], root: str, underlying: str = "SPX") -> None:
    """Slice 14, question 11: a monthly SPX option (root SPX, AM-settled) stops trading
    the day before its expiration date, which the exits don't model. Only the weekly
    root the chains price (SPXW) is traded. Slice 16: any other underlying trades its own
    root only, never an adjusted contract (SPY1), whose deliverable isn't 100 shares."""
    for p in picked:
        if p.symbol[:6].strip() != root:
            raise Unsafe(off_root(p.symbol, root, underlying))


def off_root(symbol: str, root: str, underlying: str) -> str:
    """Why a leg on another root isn't traded (adopt.held_problem says the same)."""
    if underlying.upper() == "SPX":
        return f"{symbol.strip()} is a monthly SPX option (AM-settled); the bot trades {root} only."
    return f"{symbol.strip()} isn't a standard {underlying} option; the bot trades {root} only."


def _sanity(
    picked: tuple[Picked, ...], spot: Decimal, placed: frozenset[int] = frozenset()
) -> None:
    """Tracker2's _sanity_check: no in-the-money short, no short call below a short
    put, no leg twice. A short call and put on one strike (an iron fly or straddle's
    body) is at the money by design, so that strike isn't refused as in the money.
    A short `placed` by an offset (positions; offset_short) sits where it was set, so
    in the money is the setup (an ITM diagonal), not a missed target (#206, decision
    0082; Tracker2 c670b556); the stale-chain check at 0.90 delta still applies, so such
    a short with no delta to check is NoData, never a guess. A long inside its short is
    a debit now, not a mistake here; a group that rests its stop on that short is refused
    at entry instead (inverted_group). |delta| needs no check for out-of-the-money
    shorts: they can't reach 0.90."""
    if len({p.symbol for p in picked}) != len(picked):
        raise Unsafe("Two legs landed on the same option.")
    shorts = [p for p in picked if p.side is Side.SELL]
    calls = [p.strike for p in shorts if p.right == "call"]
    puts = [p.strike for p in shorts if p.right == "put"]
    body = None
    if calls and puts:
        if min(calls) < max(puts):
            raise Unsafe(
                f"Inverted: the short call at {min(calls):f} is below the short put "
                f"at {max(puts):f}."
            )
        if min(calls) == max(puts):
            body = min(calls)
    for p in shorts:
        if p.strike != body and not _out_of_the_money(p.strike, p.right, spot):
            if p.position not in placed:
                raise Unsafe(f"The short {p.right} at {p.strike:f} is in the money.")
            if p.delta is None:
                raise NoData(
                    f"The short {p.right} at {p.strike:f} is in the money and has no delta "
                    "to check it against a stale chain; not entered."
                )
        if p.delta is not None and abs(p.delta) >= STALE_SHORT_DELTA:
            raise Unsafe(
                f"The short {p.right} at {p.strike:f} has a delta of {_magnitude(abs(p.delta))}: "
                "the chain looks stale or the target can't be reached today."
            )


def inverted_group(bot: BotDef, picked: tuple[Picked, ...]) -> None:
    """Raise Unsafe for a group with one sold leg that prices as a debit or nothing at its
    own mids inside a trade that prices as a credit: slice 12's inverted-side refusal
    (Tracker2's _sanity_check), group by group (final review I1). Its stop is worked out on
    that short, which would rise while the group wins. Decision 0048: a trade that prices
    as a debit has no stop on a short (its stops are watched on the groups' P&L, the
    fall-back), so it isn't checked."""
    if entry_price(picked, Decimal(0)) <= 0:
        return
    for group in bot.groups:
        own = [p for p in picked if p.group == group.position]
        if not own or sum(1 for p in own if p.side is Side.SELL) != 1:
            continue
        legs = [exit_math.GroupLeg(p.symbol, p.side, p.ratio) for p in own]
        entry = exit_math.group_entry(legs, {p.symbol: p.mid for p in own})
        if entry is not None and entry <= 0:
            raise Unsafe(f"Inverted: the {group.name} prices as a debit.")


def too_close_to_even(mid: Decimal) -> str | None:
    """Why a trade whose mids net to `mid` (+ a credit) isn't entered: under the smallest
    credit either way it is too close to even to take a side (decision 0048). None if not."""
    if abs(mid) >= MIN_CREDIT:
        return None
    return f"Priced too close to even ({abs(mid).quantize(Decimal('0.01'))}); not entered."


def entry_price(picked: tuple[Picked, ...], start_offset: Decimal) -> Decimal:
    """Signed: + a credit. The sum of mids x ratio, less the starting offset."""
    total = sum(((p.mid if p.side is Side.SELL else -p.mid) * p.ratio for p in picked), Decimal(0))
    return total - start_offset


def requote(
    picked: tuple[Picked, ...], quotes: Mapping[str, tuple[Decimal | None, Decimal | None]]
) -> tuple[tuple[Picked, ...], list[str]]:
    """#205, Tracker2's entry_requote.requote: the legs repriced from fresh (bid, ask)
    quotes by symbol, with a note for each leg whose mid moved. One stale or wide quote
    in the chain snapshot moves the mid the chase starts from, further than its steps
    may reach. Only when every leg has a sane two-sided quote (bid >= 0, ask > 0, bid
    <= ask); otherwise the chain's prices stand, so a quote outage never blocks an
    entry. The strikes and deltas they were picked on never change."""
    fresh = []
    for p in picked:
        bid, ask = quotes.get(p.symbol) or (None, None)
        if bid is None or ask is None or bid < 0 or ask <= 0 or bid > ask:
            return picked, []
        fresh.append((bid, ask))
    out, notes = [], []
    for p, (bid, ask) in zip(picked, fresh, strict=True):
        mid = (bid + ask) / 2
        if mid == p.mid:
            out.append(p)
            continue
        notes.append(
            f"leg {p.position + 1} requoted at entry: {_cents(bid)} x {_cents(ask)}, "
            f"mid {_cents(mid)} (the chain had {_cents(p.mid)})"
        )
        out.append(replace(p, mid=mid))
    return tuple(out), notes


def _cents(value: Decimal) -> str:
    """A price as text: at least two places (2.50), more only when it has them."""
    two = value.quantize(Decimal("0.01"))
    return f"{two:f}" if two == value else f"{value.normalize():f}"


def next_price(price: Decimal, step: Decimal, debit_max: Decimal | None = None) -> Decimal | None:
    """The next entry chase price (execution._concede): one step toward a fill, signed
    like the price. A credit gives up a step and stops at $0.05; a debit pays a step
    more and stops at `debit_max` (the premium maximum), or is unbounded without one."""
    new = price - step
    if price > 0:
        return new if new >= MIN_CREDIT else None
    if debit_max is not None and -new > debit_max:
        return None
    return new
