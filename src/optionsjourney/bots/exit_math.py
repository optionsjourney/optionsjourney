"""The numbers behind a bot's exits (spec: the first automation, section 4;
Tracker2's exits.py and watch_loop.py). Pure."""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from functools import reduce

from optionsjourney.bots import futures
from optionsjourney.bots.definition import REMAINDERS
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.core.symbols import parse_occ
from optionsjourney.trading import ticks
from optionsjourney.trading.orders import Leg, Order, OrderType, Side

STALE_AFTER = timedelta(seconds=30)  # watch_loop STALE_SECONDS
# watch_loop.QUOTE_QUIET_MAX_SECONDS (#202, decision 0081): a stream sends a quote only on
# change, so while it's live (connected, its underlying updating through it) a quiet quote
# this old is still the current one; the cap still catches a silently dropped symbol.
QUIET_MAX = timedelta(seconds=300)
CONFIRM_PASSES = 2  # watch_loop CONFIRM_PASSES
REARMS_PER_DAY = 3  # rearm_daily_limit


def after_expiry(symbol: str, now: datetime) -> bool:
    """On or after the option's expiration day, once that day's session has
    closed: the broker expires its GTC stops then, and there's nothing to protect.
    An unreadable symbol is never past expiry. A futures option's date is its symbol's
    (bots slice 17); its series may expire earlier that day (/GC at 13:30), never later."""
    parsed = parse_occ(symbol)
    option = parsed[1] if parsed is not None else futures.option_of(symbol)
    if option is None:
        return False
    local, expiration = now.astimezone(MARKET_TZ), option.expiration
    if local.date() != expiration:
        return local.date() > expiration
    close = session_close(expiration)
    return close is None or local.time() >= close


def split_legs(symbols: Iterable[str], now: datetime) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(expired, live): a group's legs past their expiry and the rest (bots slice 14). A
    group is "split" when both are non-empty: its nearer legs expired and the far ones
    are still held (a calendar or diagonal the day after its near expiry)."""
    expired, live = [], []
    for symbol in symbols:
        (expired if after_expiry(symbol, now) else live).append(symbol)
    return tuple(expired), tuple(live)


def stop_past_expiry(anchor: str | None, legs: Iterable[str], now: datetime) -> bool:
    """A stop's options are past expiry: a resting stop's anchor (`anchor`, None for a
    group the app watches on its P&L), or for a watched group any leg's: its P&L can't
    be read whole once one has gone (slice 14). The one rule bot_exits.stop_expired and
    integrity read."""
    if anchor:
        return after_expiry(anchor, now)
    return any(after_expiry(symbol, now) for symbol in legs)


def is_split(symbols: Iterable[str], now: datetime) -> bool:
    expired, live = split_legs(symbols, now)
    return bool(expired) and bool(live)


# A close of the whole group: a target, timed close or close you asked for that filled,
# or a timed close or close that found nothing held (timing.CLOSED without the stop).
_CLOSED_WHOLE = (("target", "filled"), ("timed", "filled"), ("timed", "expired"),
                 ("close", "filled"), ("close", "expired"))  # fmt: skip


def closed_whole(rows: Iterable[tuple[str, str]]) -> bool:
    """Whether a close already took the whole group (its (kind, state) rows): nothing of
    it is left to settle once its nearer legs expire (slice 14)."""
    return any(row in _CLOSED_WHOLE for row in rows)


def round_stop(short_symbol: str, raw: Decimal) -> tuple[Decimal, Decimal]:
    """(tick, trigger): `raw` rounded DOWN to the short's single-leg tick, once,
    whatever produced it (exits._tick_and_round_or_fallback): a lower trigger
    fires sooner. A raw of 0 or less stays at or below 0, for the caller to refuse."""
    probe = Order((Leg(short_symbol, Side.BUY),), 1, False, OrderType.MARKET)
    tick = ticks.order_tick(probe, raw, {})
    return tick, (raw / tick).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick


def recent(quote_at: datetime, now: datetime) -> bool:
    """A quote young enough to act on (STALE_AFTER)."""
    return now - quote_at <= STALE_AFTER


def wing_limit(long_symbol: str, price: Decimal) -> Decimal:
    """A sell price for the wing on its own tick, rounded down (toward a fill)
    and never below one tick (remainder.close_long_leg)."""
    probe = Order((Leg(long_symbol, Side.SELL),), 1, False, OrderType.MARKET)
    floor = ticks.order_tick(probe, Decimal(0), {})
    tick = ticks.order_tick(probe, price, {})
    rounded = (price / tick).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick
    return max(rounded, floor)


def remainder_mode(configured: str, leave_paused: bool) -> str:
    """What happens to the wing (remainder._effective_remainder_mode): "leave"
    only while it isn't paused; anything unknown is sold now, never stranded."""
    if configured == "leave":
        return "close_market" if leave_paused else "leave"
    return configured if configured in REMAINDERS else "close_market"


def fresh(quote_at: datetime, now: datetime, filled_at: datetime, quiet_ok: bool = False) -> bool:
    """A quote to act on: newer than the fill, and under STALE_AFTER old, or under QUIET_MAX
    while its stream is live (`quiet_ok`, watch_loop._quote_fresh)."""
    return now - quote_at <= (QUIET_MAX if quiet_ok else STALE_AFTER) and quote_at > filled_at


def buy_limit(symbol: str, cost: Decimal) -> Decimal:
    """A single-leg buy-to-close at `cost`, signed (negative), on the leg's own tick,
    rounded toward a fill (paying more), never less than one tick (the leg-out's
    short, exit_chase._Chase.submit)."""
    probe = Order((Leg(symbol, Side.BUY),), 1, False, OrderType.MARKET)
    cost = max(cost, ticks.order_tick(probe, Decimal(0), {}))
    return ticks.round_price(-cost, ticks.order_tick(probe, -cost, {}))


# --- any-legs groups (bots slice 13). Every price is per unit of the group: its legs'
# ratios over their gcd. Signed like the entry: + a credit. ---


@dataclass(frozen=True)
class GroupLeg:
    symbol: str
    side: Side
    ratio: int  # contracts per unit of the group: the leg's bot ratio over the group's gcd


def row_legs(row) -> tuple[GroupLeg, ...]:
    """An exit row's legs per unit, duck-typed over a BotExit (`legs`, and the old
    `short_symbol`/`long_symbol`). Rows with no `legs` (test fixtures, rows a crash
    wrote mid-upgrade) fall back to the old short/long pair at ratio 1."""
    if row.legs:
        return tuple(
            GroupLeg(leg["symbol"], Side(leg["side"]), int(leg["ratio"])) for leg in row.legs
        )
    return (GroupLeg(row.short_symbol, Side.SELL, 1), GroupLeg(row.long_symbol, Side.BUY, 1))


def group_legs(run_legs: Iterable[Mapping], position: int) -> tuple[tuple[GroupLeg, ...], int]:
    """exit_chase.ratio_quantities: the group's ratios over their gcd. Every price
    here is per unit of that reduced ratio (exits._short_premium's "PER UNIT")."""
    own = [leg for leg in run_legs if leg["group"] == position]
    gcd = reduce(math.gcd, (int(leg.get("ratio", 1)) for leg in own), 0) or 1
    return tuple(
        GroupLeg(leg["symbol"], Side(leg["side"]), int(leg.get("ratio", 1)) // gcd) for leg in own
    ), gcd


def anchor_of(legs: Sequence[GroupLeg]) -> GroupLeg | None:
    """The group's one SELL leg, if it has exactly one."""
    sells = [leg for leg in legs if leg.side is Side.SELL]
    return sells[0] if len(sells) == 1 else None


def group_entry(legs: Sequence[GroupLeg], fills: Mapping[str, Decimal]) -> Decimal | None:
    """Per unit, + a credit: sell fills x ratio less buy fills x ratio (the spec's
    group_entry). None while any leg has no fill."""
    total = Decimal(0)
    for leg in legs:
        fill = fills.get(leg.symbol)
        if fill is None:
            return None
        total += fill * leg.ratio if leg.side is Side.SELL else -fill * leg.ratio
    return total


def leg_mid(bid: Decimal | None, ask: Decimal | None, sell: bool) -> Decimal | None:
    """A leg's mid, or None when its quote isn't usable: the one "usable mid" rule
    (Tracker2's watch_rules.leg_mid and close_value). A bid of 0 or more and an ask
    that doesn't cross it; the ask above 0, so a bid of 0 under a positive ask is
    usable (a nearly worthless short, exactly when a stop should fire: mid ask / 2).
    A bought leg quoted 0/0 is worthless and counts 0; a sold leg at 0/0 has no mid."""
    if bid is None or ask is None or bid < 0 or ask < bid:
        return None
    if ask <= 0:
        return None if sell else Decimal(0)
    return (bid + ask) / 2


def group_value(
    legs: Sequence[GroupLeg], quotes: Mapping[str, object], natural: bool = False
) -> Decimal | None:
    """What closing the group costs now, per unit, signed like the entry (+ closing
    pays, - closing receives): watch_rules.close_value with the sign flipped to the
    entry's. Buying a sell leg back pays, selling a buy leg receives.
    Mid: every leg needs a usable mid (leg_mid). Natural (exit_chase.combo_prices): a
    sell leg at its ask (needs one above 0), a buy leg at its bid (none or 0 counts 0).
    `quotes` hold objects with .bid/.ask."""
    total = Decimal(0)
    for leg in legs:
        quote = quotes.get(leg.symbol)
        bid = getattr(quote, "bid", None)
        ask = getattr(quote, "ask", None)
        sell = leg.side is Side.SELL
        if natural:
            if sell:
                if ask is None or ask <= 0:
                    return None
                price = ask
            else:
                price = bid if bid is not None and bid > 0 else Decimal(0)
        else:
            price = leg_mid(bid, ask, sell)
            if price is None:
                return None
        total += price * leg.ratio if sell else -price * leg.ratio
    return total


def natural_fresh(
    legs: Sequence[GroupLeg], quotes: Mapping[str, object], now: datetime, filled_at: datetime,
    quiet_ok: bool = False,
) -> bool:  # fmt: skip
    """A group's natural value can be acted on (watch_loop's fresh-quote rule, slice 1's
    short and wing rule over any legs): every sell leg has a fresh ask above 0; a buy
    leg may have no quote (it sells for 0), but a quote that exists must be fresh.
    Fresh: recent (QUIET_MAX while the streams are live: `quiet_ok`, #202), and from after
    the fill."""
    for leg in legs:
        quote = quotes.get(leg.symbol)
        if quote is None:
            if leg.side is Side.SELL:
                return False
            continue
        if not fresh(quote.at, now, filled_at, quiet_ok):
            return False
        if leg.side is Side.SELL and (quote.ask is None or quote.ask <= 0):
            return False
    return True


def group_pnl(entry: Decimal, value: Decimal) -> Decimal:
    """Per unit: what was taken in less what closing costs."""
    return entry - value


def receives(value: Decimal, entry: Decimal) -> bool:
    """Closing collects a credit: a value below 0, or exactly 0 on a debit entry
    (a worthless long is sold for a tick, not bought back)."""
    return value < 0 or (value == 0 and entry < 0)


def _probe(legs: Sequence[GroupLeg]) -> Order:
    """The closing order's shape, for its tick: every leg reversed."""
    closing = tuple(
        Leg(leg.symbol, Side.BUY if leg.side is Side.SELL else Side.SELL) for leg in legs
    )
    return Order(closing, 1, False, OrderType.MARKET)


def _floor_tick(legs: Sequence[GroupLeg]) -> Decimal:
    return ticks.order_tick(_probe(legs), Decimal(0), {})


def close_price(legs: Sequence[GroupLeg], value: Decimal, entry: Decimal) -> Decimal:
    """The order price that closes at `value`: -value, signed. Paying (a debit,
    negative) or receiving (a credit, positive), at least one tick either way, and
    rounded toward a fill on the combo's tick (exit_chase._Chase.submit's floor)."""
    return _priced(legs, value, receives(value, entry))


def _priced(legs: Sequence[GroupLeg], value: Decimal, receiving: bool) -> Decimal:
    """-value in the close's direction, at least one tick, rounded toward a fill."""
    floor = _floor_tick(legs)
    price = max(-value, floor) if receiving else -max(value, floor)
    return ticks.round_price(price, ticks.order_tick(_probe(legs), price, {}))


def group_mid_close_price(
    legs: Sequence[GroupLeg], quotes: Mapping[str, object], offset: Decimal, entry: Decimal
) -> Decimal | None:
    """A timed close's first price (exit_chase._Chase._run): the group's mid value
    conceded by the start offset (more to pay, or less to receive: one addition on
    the value). The direction is the mid's own, before the offset: a close that
    receives stays receiving, floored at one tick, however far the offset reaches; it
    never turns into paying to give a position away. None without a usable mid on
    every leg."""
    value = group_value(legs, quotes)
    if value is None:
        return None
    return _priced(legs, value + offset, receives(value, entry))


def natural_price(
    legs: Sequence[GroupLeg], quotes: Mapping[str, object], entry: Decimal
) -> Decimal | None:
    """The group's close at the natural (exit_chase.combo_prices); None without one."""
    value = group_value(legs, quotes, natural=True)
    return None if value is None else close_price(legs, value, entry)


def next_close_price(legs: Sequence[GroupLeg], price: Decimal, step: Decimal) -> Decimal | None:
    """One concession toward a fill (execution._concede): the same subtraction whether
    it pays more or receives less. A receiving close stops at one tick: None when it
    already sits there (a price at or below 0 would be a different trade)."""
    if price == 0:
        return None  # never a real close price (close_price floors at one tick)
    new = price - step
    if price > 0:
        floor = _floor_tick(legs)
        if price <= floor:
            return None
        new = max(new, floor)
    return ticks.round_price(new, ticks.order_tick(_probe(legs), new, {}))
