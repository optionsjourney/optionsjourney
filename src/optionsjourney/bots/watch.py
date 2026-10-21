"""Watch rules and profit actions: condition -> action over a live snapshot of one
trade (bots slice 7; Tracker2's watch_rules.py, profit_actions.py and
time_actions.close_contracts). Pure: no DB, no stream, no clock. The service
(services/bot_watch.py) builds the views, deciding what's fresh, and acts on the
outcomes; everything about WHEN a rule fires lives here.

Deltas are the option's raw delta (put negative, call positive), signed by position
as Option Omega does: a short put tested reads +, a short call tested reads −.
Thresholds are whole numbers (65, -65), compared strictly, as are the S/L ratio's.
The underlying move and a tested short are inclusive (Option Omega's "touched").
Stale or unusable data never fires a rule: it's reported skipped."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import ROUND_FLOOR, Decimal

from optionsjourney.bots import exit_math
from optionsjourney.bots.definition import BotDef, GroupDef, ProfitActionDef
from optionsjourney.trading.orders import Side

GREEKS_STALE_AFTER = timedelta(seconds=150)  # watch_loop.GREEKS_STALE_SECONDS
STALE_ALERT_AFTER = timedelta(seconds=60)  # watch_loop.STALE_ALERT_SECONDS
STALE_SKIPS = ("stale", "incomplete")  # what counts toward the "paused" alert
# Option quotes in the first minute are wide enough to fake a breach (Tracker2's
# guards.OPENING_PAUSE_SECONDS: on 2026-10-02 at 09:30:05 three runs read their S/L
# ratio off opening mids and fired whole-trade closes). Every rule here pauses: the
# only kinds Tracker2 keeps live then are its $ stops, which this app doesn't watch.
OPENING_PAUSE = timedelta(seconds=60)
SESSION_OPEN = time(9, 30)
# Tracker2's watch_loop.HOLD_SECONDS / HOLD_KINDS (#201, decision 0081): the delta and S/L
# ratio rules read single quotes' mids and Greeks, so a few seconds of one bad quote could
# close the whole trade. Their breach must also last this long, every pass of it breached
# (a clean pass restarts the clock). The other rules and the profit actions stay fast.
HOLD = timedelta(seconds=30)
HOLD_RULES = ("position_delta", "sl_ratio")  # and every leg delta exit ("leg_delta:N")


def must_hold(rule: str) -> bool:
    """Whether a rule's breach must last HOLD as well as CONFIRM_PASSES passes."""
    return rule in HOLD_RULES or rule.startswith("leg_delta:")


def confirmed(rule: str, passes: int, since: datetime, now: datetime) -> bool:
    """Whether a breach held `passes` passes in a row, the first at `since`, now confirms
    (watch_loop._confirmed and _held): CONFIRM_PASSES passes, and for a rule that
    must_hold, HOLD since its first pass."""
    if passes < exit_math.CONFIRM_PASSES:
        return False
    return not must_hold(rule) or now - since >= HOLD


def in_opening_pause(now_et: datetime) -> bool:
    """Whether New York wall-clock `now_et` is in the session's first minute
    (guards.in_opening_pause)."""
    opened = datetime.combine(now_et.date(), SESSION_OPEN, tzinfo=now_et.tzinfo)
    return opened <= now_et < opened + OPENING_PAUSE


NAMES = {
    "position_delta": "Delta exit",
    "sl_ratio": "S/L ratio exit",
    "underlying_move": "Underlying move",
    "short_put_tested": "Short put tested",
    "short_call_tested": "Short call tested",
}  # watch_loop._EXIT_NAMES


@dataclass(frozen=True)
class LegView:
    """One open leg of the trade, as the stream has it now."""

    side: str  # "sell" or "buy"
    right: str  # "put" or "call"
    strike: Decimal | None  # None: unknown, and a rule needing it is skipped as stale
    ratio: int
    bid: Decimal | None
    ask: Decimal | None
    delta: Decimal | None  # the option's raw delta
    quote_fresh: bool
    delta_fresh: bool
    # Slice 19: the bot leg it is (its position), for a leg delta exit; None when the
    # run's leg there isn't that bot leg's side and type (leg_rule_mismatches).
    position: int | None = None


@dataclass(frozen=True)
class SpotView:
    """The underlying's last trade, in its own points, and whether it's fresh. `symbol`
    names it in the messages (watch_rules.SpotView.symbol; slice 16)."""

    price: Decimal | None
    fresh: bool
    symbol: str = "SPX"


@dataclass(frozen=True)
class Rules:
    """A bot's watch rules in force (None: that part off)."""

    position_delta: tuple[Decimal | None, Decimal | None] | None = None  # (below, above)
    sl_ratio: tuple[Decimal | None, Decimal | None] | None = None
    up_pct: Decimal | None = None
    down_pct: Decimal | None = None
    put_pts: Decimal | None = None
    call_pts: Decimal | None = None
    # Slice 19 (BotLeg.exit_delta_below/above): (leg position, below, above) per leg
    leg_delta: tuple[tuple[int, Decimal | None, Decimal | None], ...] = ()


@dataclass(frozen=True)
class Outcome:
    rule: str
    fired: bool
    skipped: str | None  # "stale", "bad_quote", "no_entry", "incomplete", or None
    detail: str

    @property
    def name(self) -> str:
        if self.rule.startswith("leg_delta:"):  # slice 19
            return f"Leg {int(self.rule.split(':')[1]) + 1} delta exit"
        return NAMES.get(self.rule, self.rule)


def rules_of(bot: BotDef) -> Rules | None:
    """The bot's ticked watch rules, or None when none is on (watch_loop.config_for,
    underlying_watch)."""

    def pair(on, below, above):
        return (below, above) if on and (below is not None or above is not None) else None

    under = bot.use_underlying_exit
    rules = Rules(
        position_delta=pair(bot.use_delta_exit, bot.position_delta_below,
                            bot.position_delta_above),
        sl_ratio=pair(bot.use_sl_ratio_exit, bot.sl_ratio_below, bot.sl_ratio_above),
        up_pct=bot.underlying_up_pct if under else None,
        down_pct=bot.underlying_down_pct if under else None,
        put_pts=bot.short_put_tested_pts if under else None,
        call_pts=bot.short_call_tested_pts if under else None,
        leg_delta=tuple(
            (leg.position, leg.exit_delta_below, leg.exit_delta_above) for leg in bot.legs
            if bot.use_delta_exit
            and (leg.exit_delta_below is not None or leg.exit_delta_above is not None)
        ),
    )  # fmt: skip
    return None if rules == Rules() else rules


@dataclass(frozen=True)
class TradeWatch:
    """One trade's own watch rules (bots slice 21; Tracker2's BotRunWatchOverride): Use
    delta (the position's below/above, each leg's by its position in the trade) and Use S/L
    ratio. They replace the bot's delta and ratio rules for that trade."""

    use_delta: bool = False
    position_delta: tuple[Decimal | None, Decimal | None] = (None, None)
    leg_deltas: tuple[tuple[int, Decimal | None, Decimal | None], ...] = ()
    use_sl_ratio: bool = False
    sl_ratio: tuple[Decimal | None, Decimal | None] = (None, None)


def rules_for(bot: BotDef, mine: TradeWatch | None) -> Rules | None:
    """The rules in force for one trade (watch_loop.config_for with its override row, then
    with_underlying): the trade's own delta and ratio rules when it has them, the bot's
    underlying move and tested shorts either way ("Strategy-only"). None when nothing is on."""
    if mine is None:
        return rules_of(bot)

    def pair(on, values):
        return values if on and any(v is not None for v in values) else None

    under = bot.use_underlying_exit
    rules = Rules(
        position_delta=pair(mine.use_delta, mine.position_delta),
        sl_ratio=pair(mine.use_sl_ratio, mine.sl_ratio),
        up_pct=bot.underlying_up_pct if under else None,
        down_pct=bot.underlying_down_pct if under else None,
        put_pts=bot.short_put_tested_pts if under else None,
        call_pts=bot.short_call_tested_pts if under else None,
        leg_delta=tuple(
            (position, below, above) for position, below, above in mine.leg_deltas
            if mine.use_delta and (below is not None or above is not None)
        ),
    )  # fmt: skip
    return None if rules == Rules() else rules


def signed_delta(leg: LegView) -> Decimal | None:
    if leg.delta is None:
        return None
    return -leg.delta if leg.side == "sell" else leg.delta


def leg_mid(leg: LegView) -> Decimal | None:
    """The mid of a usable quote (exit_math.leg_mid). A bid of 0 with a positive ask is
    usable: a nearly worthless short is exactly when a ratio rule should fire. The
    ratio needs a real price on every leg, so a 0/0 quote has none here."""
    return exit_math.leg_mid(leg.bid, leg.ask, sell=True)


def position_delta(legs) -> Decimal | None:
    total = Decimal(0)
    for leg in legs:
        d = signed_delta(leg)
        if d is None:
            return None
        total += d * leg.ratio
    return total


def sl_ratio(legs) -> Decimal | None:
    """Σ short mids ÷ Σ long mids, or None without a short and a long, an unusable
    quote, or no long premium."""
    if {leg.side for leg in legs} != {"sell", "buy"}:
        return None
    short = long_ = Decimal(0)
    for leg in legs:
        mid = leg_mid(leg)
        if mid is None:
            return None
        if leg.side == "sell":
            short += mid * leg.ratio
        else:
            long_ += mid * leg.ratio
    return None if long_ <= 0 else short / long_


def short_distance(right: str, strike: Decimal, spot: Decimal) -> Decimal:
    """Points a short is out of the money: positive = out, negative = past the strike."""
    return strike - spot if right == "call" else spot - strike


def _breach(value: Decimal, bounds) -> tuple[str, Decimal] | None:
    below, above = bounds
    if below is not None and value < below:
        return "<", below
    if above is not None and value > above:
        return ">", above
    return None


def _num(value: Decimal) -> str:
    d = value.normalize()
    return f"{d.quantize(Decimal(1)) if d == d.to_integral() else d:f}"


def _signed(value: Decimal, places: str = "0.1") -> str:
    q = value.quantize(Decimal(places))
    return f"+{q:f}" if q > 0 else f"{q:f}"


def evaluate(legs, rules: Rules, spot: SpotView | None, entry_spot: Decimal | None):
    """Every rule's Outcome over the trade's open legs (watch_rules.evaluate)."""
    out = _leg_deltas(legs, rules)
    if rules.position_delta is not None and legs:
        value = position_delta(legs)
        if value is None or not all(leg.delta_fresh for leg in legs):
            out.append(Outcome("position_delta", False, "stale", "position delta: no fresh delta"))
        else:
            whole = value * 100
            hit = _breach(whole, rules.position_delta)
            detail = f"position delta {_signed(whole)}"
            if hit:
                detail += f" {hit[0]} {_num(hit[1])}"
            out.append(Outcome("position_delta", hit is not None, None, detail))
    if rules.sl_ratio is not None and legs:
        if {leg.side for leg in legs} != {"sell", "buy"}:
            out.append(Outcome("sl_ratio", False, "incomplete", "S/L ratio: no short or no long"))
        elif not all(leg.quote_fresh for leg in legs):
            out.append(Outcome("sl_ratio", False, "stale", "S/L ratio: no fresh quotes"))
        else:
            ratio = sl_ratio(legs)
            if ratio is None:
                out.append(Outcome("sl_ratio", False, "bad_quote", "S/L ratio: unusable quote"))
            else:
                hit = _breach(ratio, rules.sl_ratio)
                detail = f"S/L ratio {ratio.quantize(Decimal('0.001'))}"
                if hit:
                    detail += f" {hit[0]} {_num(hit[1])}"
                out.append(Outcome("sl_ratio", hit is not None, None, detail))
    return out + _underlying(legs, rules, spot, entry_spot)


def _leg_deltas(legs, rules: Rules) -> list[Outcome]:
    """Slice 19 (watch_rules.evaluate's leg_delta, _delta_outcome): each leg with a
    threshold, its raw delta signed by position, x 100, compared strictly. A leg the trade
    doesn't hold (closed), or whose run leg isn't that bot leg, is left out; a stale delta
    is skipped."""
    out = []
    by_position = {leg.position: leg for leg in legs if leg.position is not None}
    for position, below, above in rules.leg_delta:
        leg = by_position.get(position)
        if leg is None:
            continue
        rule, label = f"leg_delta:{position}", f"leg {position + 1}"
        value = signed_delta(leg)
        if value is None or not leg.delta_fresh:
            out.append(Outcome(rule, False, "stale", f"{label} delta: no fresh delta"))
            continue
        whole = value * 100
        hit = _breach(whole, (below, above))
        detail = f"{label} delta {_signed(whole)}"
        if hit:
            detail += f" {hit[0]} {_num(hit[1])}"
        out.append(Outcome(rule, hit is not None, None, detail))
    return out


def _underlying(legs, rules: Rules, spot: SpotView | None, entry_spot: Decimal | None):
    """The underlying move and tested shorts (watch_rules.evaluate_underlying)."""
    out = []
    fresh = spot is not None and spot.fresh and spot.price is not None
    name = spot.symbol if spot is not None else "SPX"
    for right, pts in (("put", rules.put_pts), ("call", rules.call_pts)):
        if pts is None:
            continue
        shorts = [leg for leg in legs if leg.side == "sell" and leg.right == right]
        if not shorts:
            continue  # that side is closed: nothing to watch
        rule = f"short_{right}_tested"
        if any(leg.strike is None for leg in shorts):
            out.append(Outcome(rule, False, "stale", f"short {right}: its strike is unknown"))
            continue
        if not fresh:
            out.append(Outcome(rule, False, "stale", f"short {right}: no fresh {name} price"))
            continue
        nearest = min(shorts, key=lambda leg: short_distance(right, leg.strike, spot.price))
        dist = short_distance(right, nearest.strike, spot.price)
        hit = dist <= pts
        pts_text = _num(dist.quantize(Decimal("0.01")))
        pts_text = f"+{pts_text}" if dist > 0 else pts_text
        detail = (f"short {right} {_num(nearest.strike)} is {pts_text} pts from "
                  f"{name} {spot.price.quantize(Decimal('0.01'))}")  # fmt: skip
        if hit:
            detail += f" (≤ {_num(pts)}: tested)"
        out.append(Outcome(rule, hit, None, detail))
    if rules.up_pct is not None or rules.down_pct is not None:
        if not entry_spot:
            out.append(Outcome("underlying_move", False, "no_entry",
                               f"{name} move: the entry price is unknown"))  # fmt: skip
        elif not fresh:
            out.append(Outcome("underlying_move", False, "stale", f"{name} move: no fresh price"))
        else:
            move = (spot.price / entry_spot - 1) * 100
            hit = None
            if rules.up_pct is not None and move >= rules.up_pct:
                hit = f"≥ +{_num(rules.up_pct)}%"
            elif rules.down_pct is not None and -move >= rules.down_pct:
                hit = f"≤ -{_num(rules.down_pct)}%"
            cent = Decimal("0.01")
            detail = (f"{name} {spot.price.quantize(cent)} is {_signed(move, '0.01')}% from "
                      f"the entry {entry_spot.quantize(cent)}")  # fmt: skip
            if hit:
                detail += f" ({hit})"
            out.append(Outcome("underlying_move", hit is not None, None, detail))
    return out


# --- Profit actions --------------------------------------------------------------------


def group_pnl_now(
    entry: Decimal, views: Sequence[tuple[LegView, int]]
) -> tuple[Decimal | None, str | None]:
    """(the group's P&L per unit, None) from fresh mids: its entry less what closing
    costs at them (watch_rules._usd_stop_outcome: exit_math.group_value and group_pnl),
    rounded to 6 places; or (None, why it was skipped). `views` pairs each leg with its
    per-unit ratio. Every leg needs a fresh quote ("stale") and a usable mid
    ("bad_quote", exit_math.leg_mid's rule, read by group_value: a bid of 0 under a
    positive ask is usable, a bought leg quoted 0/0 counts 0, a sold one has no mid)."""
    if not all(view.quote_fresh for view, _ in views):
        return None, "stale"
    legs, quotes = [], {}
    for n, (view, ratio) in enumerate(views):
        legs.append(exit_math.GroupLeg(str(n), Side(view.side), int(ratio)))
        quotes[str(n)] = view  # .bid and .ask
    value = exit_math.group_value(legs, quotes)
    if value is None:
        return None, "bad_quote"
    return round(exit_math.group_pnl(entry, value), 6), None


def profit_level(group: GroupDef, entry: Decimal, at: Decimal) -> Decimal:
    """An action's level in P&L points per unit: % of the size of the group's entry (a
    credit or a debit alike), or $ as is (in the group's profit target unit,
    watch_loop.group_watches)."""
    return at if group.target_unit == "usd" else abs(entry) * at / 100


def profit_outcome(group: GroupDef, action: ProfitActionDef, number: int, entry: Decimal,
                   views: Sequence[tuple[LegView, int]]) -> Outcome:  # fmt: skip
    """Whether the group's P&L has reached the action's level, inclusive
    (watch_rules.evaluate_group): at or above a profit level, at or below a loss one.
    `views`: each leg with its per-unit ratio (group_pnl_now)."""
    rule = f"profit:{group.position}:{number}"
    label = f"{group.name} profit action {number}"
    pnl, skipped = group_pnl_now(entry, views)
    if pnl is None:
        why = "no fresh quotes" if skipped == "stale" else "unusable quote"
        return Outcome(rule, False, skipped, f"{label}: {why}")
    level = profit_level(group, entry, action.at)
    # Decision 0055: a negative At is a loss level, reached at or below it
    loss = action.at < 0
    fired = pnl <= level if loss else pnl >= level
    detail = f"{label}: P/L {pnl.quantize(Decimal('0.01'))}"
    if fired:
        detail += f" {'≤' if loss else '≥'} {level.quantize(Decimal('0.01'))}"
    return Outcome(rule, fired, None, detail)


def contracts_for(original: int, close_pct: int) -> int:
    """Contracts one profit action closes (profit_actions.contracts_for): 0 for 0%,
    else the % of the ORIGINAL contracts rounded down, at least 1."""
    if not close_pct:
        return 0
    n = (Decimal(original) * Decimal(close_pct) / 100).to_integral_value(rounding=ROUND_FLOOR)
    return max(1, int(n))


def close_contracts(close_pct: int | None, original: int, held: int) -> int:
    """A time action's close (time_actions.close_contracts): the % of the original
    contracts rounded down, never leaving fewer than one held; 0 closes nothing."""
    if not close_pct:
        return 0
    n = int(original) * int(close_pct) // 100
    return max(0, min(n, int(held) - 1))
