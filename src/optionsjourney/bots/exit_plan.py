"""Which of a group's exits rests at the broker and which the app watches, read against
the side the trade filled on (decision 0048; spec: trades take either side, sections 2-4).
Pure: bot_exits.place_protections, adopt.plan_exits and the entry's preview all read it.

Tracker2's exits.engine_side_for lets the choice matter only for a group that is one sold
option; here it applies to any group of a bot on either broker (decision 0060), and a
stop that can't rest (a debit fill) falls back to being watched by the app while the
target rests."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from optionsjourney.bots import exit_math, stops
from optionsjourney.bots.definition import BotDef, GroupDef, _pct_text
from optionsjourney.bots.watched_stops import signed
from optionsjourney.trading.orders import Side

# Stop styles measured on a credit: on a debit fill they don't translate (spec section 4).
CREDIT_ONLY = {
    "leg_premium": "a % above the short's fill",
    "closing_order": "$ off the entry credit",
}


@dataclass(frozen=True)
class GroupPlan:
    """One group's exits on one fill. `group` is the group as read against the fill (a
    target or stop it can't keep is None, a % of the credit is a % of the debit lost);
    `on_short`: a credit fill with one sold leg, so its stop is worked out on that short;
    `rests`: "stop", "target" or None (nothing rests at the broker); `stop_on`: "broker"
    (a resting stop on the short), "short" (the app watches the short's quote), "pnl" (the
    app watches the group's P&L) or None; `fallback`: Stop chosen, filled for a debit
    (section 3); `notes`: what the entry's alert says about this group."""

    group: GroupDef
    on_short: bool
    rests: str | None
    stop_on: str | None
    fallback: bool
    notes: tuple[str, ...] = ()


def credit_fill(entry_credit: Decimal | None, fallback: Decimal | None = None) -> bool:
    """Whether a trade filled for a credit: its price (+ a credit), else `fallback` (its
    legs' own fills), else a credit (what every trade before decision 0048 was taken as)."""
    price = entry_credit if entry_credit is not None else fallback
    return price is None or price > 0


def read_against_fill(
    group: GroupDef, credit: bool, co_mode: str = "off"
) -> tuple[GroupDef, tuple[str, ...]]:
    """The group's numbers as this fill reads them, and what was dropped or ignored
    (spec section 4): a % target of 100 or more can't be kept on a credit; on a debit a
    % of the credit is the same loss less 100, and the stops measured on the short's
    fill or off the credit don't translate. The overlay only acts on a credit's short."""
    notes: list[str] = []
    if (
        credit
        and group.target_unit != "usd"
        and group.target_pct is not None
        and group.target_pct >= 100
    ):
        notes.append(
            f"Opened for a credit; a {_pct_text(group.target_pct)}% target can't be kept "
            "on a credit. No target set."
        )
        group = replace(group, target_pct=None)
    if not credit and group.stop_unit != "usd" and group.stop_pct is not None:
        if group.stop_basis in CREDIT_ONLY:
            notes.append(
                f"Opened for a debit; a stop measured as {CREDIT_ONLY[group.stop_basis]} only "
                "works on a credit. No stop set."
            )
            group = replace(group, stop_pct=None)
        elif group.stop_basis == "entry_premium":
            lost = group.stop_pct - 100
            if lost > 0:
                group = replace(group, stop_basis="group_entry", stop_pct=lost)
            else:
                notes.append(
                    f"Opened for a debit; a stop at {_pct_text(group.stop_pct)}% of the credit "
                    "is no loss on a debit. No stop set."
                )
                group = replace(group, stop_pct=None)
    if not credit and co_mode != "off" and group.stop_value is not None:
        notes.append(
            "Opened for a debit; the closing order works only on a credit trade's stop on its "
            "short, so it's ignored."
        )
    return group, tuple(notes)


def plan_group(
    group: GroupDef, sides: Iterable[Side | str], credit: bool, broker: str, co_mode: str = "off"
) -> GroupPlan:
    """The spec's section 2 table for one group, the same on TastyTrade and Schwab
    (decision 0060; `broker` is read by nothing since). A credit fill with one sold leg
    rests its stop on that short unless the group chose its target (and has one); every
    other group rests its target and the app watches its stop: on the short's quote for a
    credit's one sold leg, else on the group's P&L (decision 0039). A debit fill on a group
    that chose Stop is the fall-back (section 3)."""
    read, notes = read_against_fill(group, credit, co_mode)
    on_short = credit and sum(1 for side in sides if Side(side) is Side.SELL) == 1
    has_stop, has_target = read.stop_value is not None, read.target_value is not None
    stop_on = ("short" if on_short else "pnl") if has_stop else None
    if on_short and has_stop and (group.broker_exit != "target" or not has_target):
        return GroupPlan(read, on_short, "stop", "broker", False, notes)
    fallback = not credit and has_stop and group.broker_exit != "target"
    return GroupPlan(read, on_short, "target" if has_target else None, stop_on, fallback, notes)


def stop_for(
    plan: GroupPlan,
    legs: Sequence[exit_math.GroupLeg],
    fills: Mapping[str, Decimal],
    entry_credit: Decimal | None,
    co_mode: str,
    co_amount: Decimal | None,
) -> stops.StopCalc | None:
    """The group's stop on this fill, or None (no stop, or its short has no fill): on the
    short (stops.stop_calc, resting or watched there) when `plan.on_short`, else a P&L
    level the app watches (stops.watched_calc). `fills`: symbol -> price per contract
    (the fills, or the mids for a preview); `entry_credit`: the whole trade's per unit."""
    if plan.stop_on is None:
        return None
    entry = exit_math.group_entry(legs, fills)
    if plan.on_short:
        anchor = exit_math.anchor_of(legs)
        fill = fills.get(anchor.symbol) if anchor is not None else None
        if fill is None:
            return None
        return stops.stop_calc(
            plan.group, co_mode, co_amount, anchor.symbol, fill, entry_credit,
            ratio=anchor.ratio, group_entry=entry,
        )  # fmt: skip
    return None if entry is None else stops.watched_calc(plan.group, entry)


def target_for(
    plan: GroupPlan, legs: Sequence[exit_math.GroupLeg], fills: Mapping[str, Decimal]
) -> tuple[Decimal | None, str | None]:
    """(the group's close-at, a problem) on this fill (stops.target_limit), or (None, None)
    with no target or no fill."""
    entry = exit_math.group_entry(legs, fills)
    if plan.group.target_value is None or entry is None:
        return None, None
    return stops.target_limit(plan.group, entry)


# A debit fill on a group that chose Stop (spec section 3): the note the run page shows on it
FALLBACK_NOTE = (
    "Opened for a debit, so its stop is watched by the bot: a stop can't rest at the broker "
    "on a debit spread."
)
CENT = Decimal("0.01")
BROKER_NAMES = {"tastytrade": "TastyTrade", "schwab": "Schwab"}


def _money(value: Decimal) -> str:
    return f"{abs(value).quantize(CENT)}"


def with_pairing(plan: GroupPlan, group: GroupDef, names: Mapping[int, str]) -> GroupPlan:
    """Decision 0047's pairing runs only after a stop resting at the broker fills: on a
    trade where this group's stop is the app's, the plan says it won't run."""
    if group.close_on_stop is None or plan.stop_on in (None, "broker"):
        return plan
    other = names.get(group.close_on_stop, f"Group {group.close_on_stop + 1}")
    note = f"Its stop is watched by the bot, so closing {other} after it doesn't run on this trade."
    return replace(plan, notes=(*plan.notes, note))


def group_words(
    name: str,
    plan: GroupPlan,
    calc: stops.StopCalc | None,
    limit: Decimal | None,
    problem: str | None,
    broker: str = "TastyTrade",
) -> str:
    """One group's exits in words, what rests first (spec section 6): "Call fly: target
    2.78 rests at TastyTrade; stop watched by the bot at P&L −0.93." and its notes."""
    if plan.group.target_value is None or limit is None:
        target = "no target"
    elif problem is not None:
        target = f"no target ({problem})"
    elif plan.rests == "target":
        target = f"target {_money(limit)} rests at {broker}"
    else:
        target = f"target {_money(limit)} watched by the bot"
    if plan.stop_on is None or calc is None:
        stop = "no stop"
    elif calc.problem is not None:
        stop = f"no stop ({calc.problem})"
    elif plan.stop_on == "broker":
        stop = f"stop {calc.trigger} rests at {broker}"
    elif plan.stop_on == "short":
        stop = f"stop watched by the bot at {calc.trigger}"
    else:
        stop = f"stop watched by the bot at P&L {signed(calc.trigger)}"
    first, second = (stop, target) if plan.rests == "stop" else (target, stop)
    return " ".join((f"{name}: {first}; {second}.", *plan.notes))


def preview(
    bot: BotDef, run_legs: Sequence[Mapping], mids: Mapping[str, Decimal], price: Decimal
) -> str:
    """What an entry priced at `price` (+ a credit, at the mids) would set, for Preview now
    and every run's record (spec section 6). `run_legs`: the run's legs (symbol, side,
    ratio, group); `mids`: symbol -> mid per contract."""
    side = f"Priced as a {'credit' if price > 0 else 'debit'} of {_money(price)}."
    return " ".join((side, *group_lines(bot, run_legs, mids, price)))


def group_lines(
    bot: BotDef, run_legs: Sequence[Mapping], mids: Mapping[str, Decimal], price: Decimal
) -> list[str]:
    """Each group's exits in words (group_words) on an entry priced at `price` at the
    mids: the Preview order page lists them, preview joins them."""
    credit = price > 0
    broker = BROKER_NAMES.get(bot.broker, bot.broker)
    names = {g.position: g.name for g in bot.groups}
    lines = []
    for group in sorted(bot.groups, key=lambda g: g.position):
        legs, _ = exit_math.group_legs(run_legs, group.position)
        if not legs:
            continue
        plan = plan_group(group, (leg.side for leg in legs), credit, bot.broker, bot.stop_co_mode)
        plan = with_pairing(plan, group, names)
        calc = stop_for(plan, legs, mids, price, bot.stop_co_mode, bot.stop_co_amount)
        limit, problem = target_for(plan, legs, mids)
        lines.append(group_words(group.name, plan, calc, limit, problem, broker))
    return lines


def _usable(calc: stops.StopCalc | None) -> bool:
    return calc is not None and calc.trigger is not None and calc.problem is None


def entry_alert(
    bot_name: str,
    entry: Decimal | None,
    groups: Sequence[tuple[str, GroupPlan, stops.StopCalc | None, Decimal | None]],
    several: bool,
    broker: str = "TastyTrade",
) -> tuple[str, str] | None:
    """(title, detail) of the one alert a fill raises when its exits differ from what the
    bot says (spec section 3), or None. `groups`: (name, plan, its stop, its close-at);
    `several`: name each group (a bot of more than one); `broker`: its name, as said."""
    fallback = [g for g in groups if g[1].fallback]
    noted = [g for g in groups if g[1].notes]
    if not fallback and not noted:
        return None
    size = "" if entry is None else f" of {_money(entry)}"
    lines: list[str] = []
    if fallback:
        watched = [g for g in fallback if _usable(g[2])]
        title = (
            f"{bot_name}: opened for a debit, so its stop is watched by the bot."
            if watched
            else f"{bot_name}: opened for a debit, and it has no stop."
        )
        lines.append(
            f"It opened for a debit{size}, and a stop can't rest at the broker on a debit spread."
        )
        for name, plan, calc, limit in fallback:
            lead = f"{name}: the bot" if several else "The bot"
            if not _usable(calc):  # none set (or none watched): never said to be watched
                lines.append(f"{name} has no stop." if several else "It has no stop.")
            else:
                lines.append(
                    f"{lead} watches the stop (P&L {signed(calc.trigger)}) while the app is "
                    "running and the computer is awake."
                )
            # plan.rests is None for a stop with no target: no target rests, so say nothing
            if plan.rests == "target" and limit is not None:
                lines.append(f"The profit target ({_money(limit)}) rests at {broker}.")
    else:
        side = "credit" if entry is None or entry > 0 else "debit"
        title = f"{bot_name}: opened for a {side}, so some exits changed"
    for name, plan, _, _ in noted:
        lines.extend(f"{name}: {note}" if several else note for note in plan.notes)
    return title, " ".join(lines)
