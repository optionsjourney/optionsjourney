"""The bot page's summary cards (Tracker2's automation_bot.html config cards): a few
short lines each for the legs, the entry, the exit and the chase. Pure."""

from decimal import Decimal

from optionsjourney.bots import schedule
from optionsjourney.bots.date_lists import DATE_SETS, short_label
from optionsjourney.bots.definition import BotDef, ChaseDef, GroupDef, LegDef
from optionsjourney.trading.orders import Side


def _num(value: Decimal | None) -> str:
    """A number as written, without trailing zeros."""
    if value is None:
        return "?"
    d = value.normalize()
    return f"{d.quantize(Decimal(1)) if d == d.to_integral() else d:f}"


def blackout_labels(bot: BotDef) -> list[str]:
    """Each blackout list with its checks, "FOMC (entry day + before expiration)", "Market
    holiday (before expiration)" (#207; Tracker2's _blackout_set_labels)."""
    entry, later = schedule.blackout_entry_lists(bot), schedule.blackout_expiry_lists(bot)
    out = []
    for name in DATE_SETS:
        checks = (["entry day"] if name in entry else []) + (
            ["before expiration"] if name in later else []
        )
        if checks:
            out.append(f"{short_label(name)} ({' + '.join(checks)})")
    return out


def _money(value: Decimal | None) -> str:
    return "?" if value is None else f"${value:,.2f}"


def leg_line(bot: BotDef, leg: LegDef) -> str:
    side = "short" if Side(leg.side) is Side.SELL else "long"
    if leg.pick == "premium":
        pick = _money(leg.target)
    elif leg.pick == "delta":
        pick = f"Δ{_num(leg.target)}"
    elif leg.pick == "offset":
        pick = f"{_num(leg.target)} pts from leg {(leg.link or 0) + 1}"
    else:  # spot
        pick = f"{_num(leg.target)} pts from spot"
    wide = f" ≤{_num(abs(leg.max_offset))} wide" if leg.max_offset is not None else ""
    return f"{leg.ratio}× {side} {leg.right} {pick}{wide} {leg.dte}DTE"


def legs(bot: BotDef) -> list[str]:
    return [leg_line(bot, leg) for leg in bot.legs]


def _size(bot: BotDef) -> str:
    if bot.sizing == "dollars":
        return f"Up to {_money(bot.max_per_entry_usd)} per entry"
    sizes = {t.quantity or bot.contracts for t in bot.entry_times if t.enabled}
    sizes = sizes if sizes and not bot.use_floating_entry else {bot.contracts}
    low, high = min(sizes), max(sizes)
    word = "contract" if high == 1 else "contracts"
    return f"{low} {word}" if low == high else f"{low}–{high} {word}"


def _bounds(name: str, low, high, unit: str = "") -> str:
    parts = [f"≥ {_num(low)}{unit}"] if low is not None else []
    if high is not None:
        parts.append(f"≤ {_num(high)}{unit}")
    return f"{name} {' / '.join(parts)}"


def entry(bot: BotDef) -> list[str]:
    days, times = schedule.summary(bot)
    if bot.use_floating_entry:
        when = f"Floating {times}"
    else:
        on = [t for t in bot.entry_times if t.enabled]
        when = ", ".join(
            t.time[:5] + (f" ×{t.quantity}" if t.quantity and bot.sizing != "dollars" else "")
            for t in on
        ) or "No times"
    lines = [f"{when} · {days}", _size(bot)]
    if bot.use_premium_bounds and (bot.premium_min is not None or bot.premium_max is not None):
        lines.append(_bounds("Premium", bot.premium_min, bot.premium_max))
    if bot.use_vix_level and (bot.vix_min is not None or bot.vix_max is not None):
        lines.append(_bounds("VIX", bot.vix_min, bot.vix_max))
    if bot.use_vix9d_ratio and (bot.vix9d_ratio_min is not None or bot.vix9d_ratio_max is not None):
        lines.append(_bounds("VIX9D/VIX", bot.vix9d_ratio_min, bot.vix9d_ratio_max))
    if bot.use_vix_overnight and bot.vix_overnight.any_set:
        lines.append("VIX overnight move filter")
    if bot.use_technicals and (bot.sma_mode or bot.ema_mode):
        lines.append("Technical filter")
    if bot.use_intraday_move and bot.intraday.any_set:
        lines.append("Intraday move filter")
    if bot.use_blackout_days:
        sets = blackout_labels(bot)
        lines.append("Use blackout days" + (f": {', '.join(sets)}" if sets else ""))
    if bot.max_open_trades:
        lines.append(f"Max open trades: {bot.max_open_trades}")
    if bot.max_entries_per_day:
        lines.append(f"Max entries/day: {bot.max_entries_per_day}")
    return lines


def _group_exits(group: GroupDef) -> str:
    parts = []
    if group.stop_value is not None:
        stop = _money(group.stop_usd) if group.stop_unit == "usd" else f"{_num(group.stop_pct)}%"
        parts.append(f"SL {stop}")
    if group.target_value is not None:
        target = (
            _money(group.target_usd) if group.target_unit == "usd" else f"{_num(group.target_pct)}%"
        )
        parts.append(f"PT {target}")
    return " · ".join(parts)


def exit_lines(bot: BotDef) -> list[str]:
    lines = []
    if len(bot.groups) > 1:
        lines.append(f"Leg groups ({len(bot.groups)})")
        for g in bot.groups:
            exits = _group_exits(g)
            lines.append(f"{g.name} · {exits}" if exits else g.name)
    elif bot.groups:
        lines.append(_group_exits(bot.groups[0]) or "No stop or target")
    if bot.close_time:
        lines.append(f"Close at {bot.close_time}" + (f", {bot.close_dte} DTE" if bot.close_dte else ""))
    if bot.use_time_actions:
        lines.extend(t.label for t in bot.time_actions)
    if bot.use_delta_exit:
        lines.append(_bounds("Delta", bot.position_delta_below, bot.position_delta_above))
    if bot.use_sl_ratio_exit:
        lines.append(_bounds("Short/long ratio", bot.sl_ratio_below, bot.sl_ratio_above))
    if bot.use_underlying_exit:
        if bot.underlying_up_pct is not None:
            lines.append(f"{bot.underlying} up {_num(bot.underlying_up_pct)}%")
        if bot.underlying_down_pct is not None:
            lines.append(f"{bot.underlying} down {_num(bot.underlying_down_pct)}%")
    return lines


def chase_lines(chase: ChaseDef) -> list[str]:
    return [
        f"Adjust {chase.step:.2f} every {chase.interval_seconds} seconds up to "
        f"{chase.max_tries} tries",
        f"Starting offset {chase.start_offset:.2f}",
    ]


def execution(bot: BotDef) -> list[tuple[str, list[str]]]:
    return [("Entry", chase_lines(bot.entry_chase)), ("Exit", chase_lines(bot.exit_chase))]
