"""Checking a bot before it's saved (Tracker2's validation.py, trimmed to this
slice). Pure. Returns {field: problem}; empty means valid."""

import re
from datetime import time
from decimal import Decimal

from optionsjourney.bots import underlyings
from optionsjourney.bots.date_lists import DATE_SETS, ENTRY_DAY_SETS, SPECIFIC_SETS
from optionsjourney.bots.definition import (
    ALERT_CHANNELS,
    ALERT_NAME_MAX,
    BROKER_EXITS,
    CO_MODES,
    DEFAULT_ALERT_TYPES,
    EXHAUST_MODES,
    EXIT_UNITS,
    FREQUENCIES,
    MAX_CONTRACTS,
    MAX_DEFAULT_ALERTS,
    MAX_DTE,
    MAX_GROUPS,
    MAX_LEGS,
    MAX_PROFIT_ACTIONS,
    MAX_RATIO,
    MAX_TAGS,
    MAX_TIME_ACTIONS,
    NATURAL_MINUTES,
    PICKS,
    REMAINDERS,
    RIGHTS,
    SIZINGS,
    STOP_BASES,
    TIME_ACTION_TYPES,
    WATCHED_BASES,
    WINDOW_MINUTES,
    BotDef,
    GroupDef,
    MoveLimits,
    day_cap,
    largest_ratio,
    trade_cap,
)
from optionsjourney.bots.market_filters import MODES
from optionsjourney.bots.schedule import SECOND_STEP, clock, is_date
from optionsjourney.trading.orders import Side

TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
# An entry time as stored: zero seconds are dropped (schedule.normalize_time)
ENTRY_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::([0-5][1-9]|[1-5]0))?$")
FIRST_ENTRY, LAST_ENTRY = time(9, 30), time(15, 58)  # entries stop 2 minutes before the close


BROKER_NAMES = {"tastytrade": "TastyTrade", "schwab": "Schwab"}


def _one_sold(bot: BotDef, position: int) -> bool:
    """The group has exactly one sold leg: the only kind whose stop can rest on a short (on a
    credit fill), so the only kind that takes every stop style (decision 0048)."""
    return sum(1 for leg in bot.legs_in(position) if leg.side is Side.SELL) == 1


def validate(bot: BotDef, tag_ids: set[int]) -> dict[str, str]:
    """Bots trade through TastyTrade or Schwab alike (decision 0059)."""
    problems: dict[str, str] = {}
    if not bot.name.strip() or len(bot.name) > 100:
        problems["name"] = "Give the bot a name (up to 100 characters)."
    if bot.tag_id not in tag_ids:
        problems["tag"] = "Give the bot at least one tag: its trades are filed under its tags."
    if bot.broker not in BROKER_NAMES:
        problems["broker"] = "Bots trade through TastyTrade or Schwab."
    elif not bot.account_number:
        problems["broker"] = f"Pick the {BROKER_NAMES[bot.broker]} account it trades in."
    ratio = largest_ratio(bot)
    by_contracts = bot.sizing != "dollars"  # decision 0050: a fixed value hides the count
    if by_contracts and not 1 <= bot.contracts <= MAX_CONTRACTS:
        problems["contracts"] = f"Contracts per entry must be 1 to {MAX_CONTRACTS}."
    if any(limit is not None and limit < 1 for limit in (bot.max_per_trade, bot.max_per_day)):
        problems["limits"] = (
            "Max contracts per trade and per day are at least 1, or blank for Auto."
        )
    elif by_contracts and bot.contracts * ratio > min(trade_cap(bot), day_cap(bot)):
        problems["limits"] = (
            "Contracts per entry can't be more than the limits."
            if ratio == 1
            else f"Contracts per entry × the largest leg's ratio ({ratio}) is more than the limits."
        )
    _legs(bot, problems)
    _groups(bot, problems)
    _pairing(bot, problems)
    _times(bot, problems)
    _caps(bot, problems)
    _premium(bot, problems)
    _frequency(bot, problems)
    _blackout(bot, problems)
    _closing_order(bot, problems)
    _market_filters(bot, problems)
    problem = underlyings.problem(bot.underlying)
    if problem:
        problems["underlying"] = problem
    for phase, chase in (("entry", bot.entry_chase), ("exit", bot.exit_chase)):
        problem = _chase_problem(chase, underlyings.grid(bot.underlying))
        if problem:
            problems["chase"] = f"The {phase} chase: {problem}"
            break
    _exhaust(bot, problems)
    _sizing(bot, problems)
    _strike_rules(bot, problems)
    if underlyings.futures(bot.underlying):
        _futures(bot, problems)
    _floating(bot, problems)
    _timed_close(bot, problems)
    _time_actions(bot, problems)
    _watch_rules(bot, problems)
    for group in bot.groups:
        problem = _profit_actions(group)
        if problem:
            problems[f"profit-{group.position}"] = f"{group.name or 'Group'}: {problem}"
    if len(bot.tags) > MAX_TAGS:
        problems["tags"] = f"A bot can have up to {MAX_TAGS} tags."
    _alerts(bot, problems)
    return problems


def _alerts(bot: BotDef, problems: dict[str, str]) -> None:
    """The alert name and channel, and validation.validate_default_alerts (only
    while ticked)."""
    if bot.alert_name is not None and len(bot.alert_name) > ALERT_NAME_MAX:
        problems["alert_name"] = f"The alert name is up to {ALERT_NAME_MAX} characters."
    if bot.alert_channel not in ALERT_CHANNELS:
        problems["alert_channel"] = "Choose where this bot's routine alerts go."
    problem = _default_alerts(bot)
    if problem:
        problems["default_alerts"] = problem


def _default_alerts(bot: BotDef) -> str | None:
    if not bot.use_default_alerts:
        return None
    rows = bot.default_alerts
    if not rows:
        return "Default alerts is on but there are no alert rules."
    if len(rows) > MAX_DEFAULT_ALERTS:
        return f"There can be at most {MAX_DEFAULT_ALERTS} default alerts."
    seen = set()
    for n, row in enumerate(rows, 1):
        if row.type not in DEFAULT_ALERT_TYPES:
            return f"Default alert {n}: choose P&L % or near a short strike."
        if row.type == "pnl_pct":
            if row.direction not in ("above", "below"):
                return f"Default alert {n}: choose above or below."
            if row.value is None or row.value == 0 or not -1000 <= row.value <= 1000:
                return f"Default alert {n}: the P&L % is a non-zero number, -1000 to 1000."
            key = ("pnl_pct", row.direction, row.value.normalize())
        else:
            if row.points is None or not 0 <= row.points <= 1000:
                return f"Default alert {n}: the points are 0 to 1000."
            key = ("strike_offset", row.points.normalize())
        if key in seen:
            return f"Default alert {n} is a duplicate of an earlier one."
        seen.add(key)
    return None


SESSION_OPEN, SESSION_CLOSE = time(9, 30), time(16, 0)


def _session_time(text: str | None) -> time | None:
    """An HH:MM from 09:30 to 15:59 New York, else None."""
    found = TIME.match(text or "")
    if not found:
        return None
    at = time(int(found.group(1)), int(found.group(2)))
    return at if SESSION_OPEN <= at < SESSION_CLOSE else None


def _multi_expiry(bot: BotDef) -> bool:
    """A group with legs in more than one expiration (a calendar or diagonal)."""
    return any(len({leg.dte for leg in bot.legs_in(g.position)}) > 1 for g in bot.groups)


def _timed_close(bot: BotDef, problems: dict[str, str]) -> None:
    """validate_scheduled_close: the time is the switch; the DTE is 0 or more. Every
    entry time must come before it when a trade can be due the day it opens (its
    nearest DTE within close_dte; slice 14): one entered at or after the close time
    would be closed as soon as it's in."""
    if bot.close_time is None:
        if _multi_expiry(bot):
            # The author's rule (2026-10-13): a calendar or diagonal never reaches its near
            # expiration. The timed close counts per group to its nearest leg, so it closes
            # on the near legs' last day at the latest.
            problems["timed_close"] = CALENDAR_CLOSES
        return
    at = _session_time(bot.close_time)
    if at is None:
        problems["timed_close"] = "The close time is from 09:30 to 15:59 (New York), like 15:45."
        return
    if bot.close_dte < 0:
        problems["timed_close"] = "Days to expiration is a whole number, 0 or more."
        return
    if bot.legs and min(leg.dte for leg in bot.legs) > bot.close_dte:
        return  # never due on the day it opens
    if bot.use_floating_entry:  # the window replaces the entry times
        if bot.floating_end and bot.floating_end >= bot.close_time:
            problems["timed_close"] = (
                f"The entry window ends at {bot.floating_end}, at or after the timed close at "
                f"{bot.close_time}: a trade entered then would be closed as soon as it's in."
            )
        return
    for entry in bot.entry_times:
        if entry.time >= bot.close_time:
            problems["timed_close"] = (
                f"Entry time {entry.time} is at or after the timed close at {bot.close_time}: "
                "that trade would be closed as soon as it's in."
            )
            return


CALENDAR_CLOSES = (
    "A calendar or diagonal must close before its near legs expire: set a timed close."
)


def trade_timed_problem(close_time: str | None, close_dte: int | None,
                        multi_expiry: bool = False) -> str | None:  # fmt: skip
    """One trade's own timed close (bots slice 21; timed_close_overrides.validate): a time
    from 09:30 to 15:59 New York, or none to turn it off for that trade (refused for a
    calendar or diagonal, the author's rule of 2026-10-13); a DTE of 0 or more, which needs
    a time."""
    if close_dte is not None and close_dte < 0:
        return "Days to expiration is a whole number, 0 or more."
    if not close_time:
        if close_dte:
            return "A DTE needs a close time."
        return CALENDAR_CLOSES if multi_expiry else None
    if _session_time(close_time) is None:
        return "The close time is from 09:30 to 15:59 (New York), like 15:45."
    return None


FUTURES_TT_ONLY = "Futures options trade through TastyTrade only."  # analyzer_entry
FUTURES_NO_STOP = "Futures: profit target only, no stop. Leave the stop blank."
FUTURES_TARGET = "Set a profit target on every group: it's a futures trade's only exit."


def _futures(bot: BotDef, problems: dict[str, str]) -> None:
    """Bots slice 17 (decision 0043; Tracker2's analyzer_entry futures path): a futures
    bot trades through TastyTrade only, sets a profit target on every group and no stop
    ("A futures run places PT only, whatever stop the strategy has configured"), and has
    none of the exits and entry rules built for equity sessions and stock-option quotes.
    It enters only from the Trade page, so its schedule is never read."""
    if bot.broker != "tastytrade":
        problems["broker"] = FUTURES_TT_ONLY
    for group in bot.groups:
        key = f"group-{group.position}"
        if group.stop_value is not None:
            problems[key] = FUTURES_NO_STOP
        elif group.target_value is None:
            problems.setdefault(key, FUTURES_TARGET)
    features = (
        ("the timed close", bot.close_time is not None),
        ("the time actions", bot.use_time_actions),
        ("the watch rules", bot.use_delta_exit or bot.use_sl_ratio_exit
         or bot.use_underlying_exit),
        ("the profit actions", any(g.use_profit_actions for g in bot.groups)),
        ("the closing order", bot.stop_co_mode != "off"),
        ("the entry window", bot.use_floating_entry),
        ("the market filters", bot.use_vix_level or bot.use_vix_overnight
         or bot.use_vix9d_ratio or bot.use_technicals or bot.use_intraday_move),
        ("the default alerts", bot.use_default_alerts),
    )  # fmt: skip
    if _multi_expiry(bot):  # a calendar needs a timed close, which a futures bot can't have
        problems["timed_close"] = (
            "A futures bot trades one expiration per group: give each group's legs one DTE."
        )
    on = [name for name, used in features if used]
    if on:
        problems["futures"] = f"A futures bot has a profit target only: turn off {', '.join(on)}."


def _floating(bot: BotDef, problems: dict[str, str]) -> None:
    """The window (floating.py; Tracker2's validation of floating_start/_end), only
    while it's on: two times like entry times, the start before the end."""
    if not bot.use_floating_entry:
        return
    times = []
    for text in (bot.floating_start, bot.floating_end):
        found = TIME.match(text or "")
        at = found and time(int(found.group(1)), int(found.group(2)))
        if not at or not FIRST_ENTRY <= at < LAST_ENTRY:
            problems["floating"] = (
                "The entry window's times are from 09:30 to 15:57 (New York), like 11:30."
            )
            return
        times.append(at)
    if times[0] >= times[1]:
        problems["floating"] = "The entry window starts before it ends."


def _time_actions(bot: BotDef, problems: dict[str, str]) -> None:
    """validate_time_actions: only while ticked."""
    if not bot.use_time_actions:
        return
    rows = bot.time_actions
    if not rows:
        problems["time_actions"] = "Time actions are on: add at least one."
        return
    if len(rows) > MAX_TIME_ACTIONS:
        problems["time_actions"] = f"There can be at most {MAX_TIME_ACTIONS} time actions."
        return
    for n, row in enumerate(rows, 1):
        problem = _action_problem(row)
        if problem:
            problems["time_actions"] = f"Time action {n}: {problem}"
            return


def _action_problem(row) -> str | None:
    if row.type not in TIME_ACTION_TYPES:
        return "pick days to expiration or days in trade."
    if row.days < 0:
        return "days is a whole number, 0 or more."
    if _session_time(row.at) is None:
        return "the time is from 09:30 to 15:59 (New York), like 14:00."
    if row.stop_pct is None and row.target_pct is None and not row.close_pct:
        return "set a stop % or a target % to move to, or a close %."
    if row.close_pct is not None and not 1 <= row.close_pct <= 99:
        return "the close % is a whole number from 1 to 99, or blank."
    if row.stop_pct is not None and not 1 <= row.stop_pct <= 1000:
        return "the stop % is between 1 and 1000, measured like the group's own % stop."
    if row.target_pct is not None and not 1 <= row.target_pct <= 1000:
        # Decision 0048: read against the fill; a credit can't keep 100% or more (exit_plan)
        return "the target is the % of the credit kept or gained on the debit, between 1 and 1000."
    return None


def _watch_rules(bot: BotDef, problems: dict[str, str]) -> None:
    """validate_full_config's delta and S/L ratio exits and validate_underlying_exit,
    each only while ticked; ticked with nothing set is refused."""
    problem = None
    if bot.use_delta_exit:
        problem = _deltas(bot)
    if problem is None and bot.use_sl_ratio_exit:
        problem = _pair("S/L ratio", bot.sl_ratio_below, bot.sl_ratio_above,
                        lambda v: v > 0, "above 0")  # fmt: skip
    if problem is None and bot.use_underlying_exit:
        values = (bot.underlying_up_pct, bot.underlying_down_pct, bot.short_put_tested_pts,
                  bot.short_call_tested_pts)  # fmt: skip
        if all(v is None for v in values):
            problem = "Underlying movement is on but nothing is set."
        elif any(v is not None and v <= 0 for v in values[:2]):
            problem = "The underlying's up and down moves are % moves above 0."
    if problem:
        problems["watch_rules"] = problem


def trade_watch_problem(mine, legs) -> str | None:
    """One trade's own watch rules (bots slice 21; watch_overrides.validate, which runs the
    strategy's validation against the trade's legs): each part only while ticked, on with
    nothing set refused, deltas from -100 to 100, ratios above 0, below under above, and
    a leg threshold only on one of the trade's legs (`legs`: their positions)."""
    in_range = lambda v: -100 <= v <= 100  # noqa: E731
    if mine.use_delta:
        known = set(legs)
        set_legs = [d for d in mine.leg_deltas if d[1] is not None or d[2] is not None]
        if mine.position_delta == (None, None) and not set_legs:
            return "Delta is on but nothing is set."
        if mine.position_delta != (None, None):
            problem = _pair("Delta", *mine.position_delta, in_range, "between -100 and 100")
            if problem:
                return problem
        for position, below, above in set_legs:
            if position not in known:
                return f"Leg {position + 1} isn't a leg of this trade."
            problem = _pair(f"Leg {position + 1}'s delta", below, above, in_range,
                            "between -100 and 100")  # fmt: skip
            if problem:
                return problem
    if mine.use_sl_ratio:
        return _pair("S/L ratio", *mine.sl_ratio, lambda v: v > 0, "above 0")
    return None


def _deltas(bot: BotDef) -> str | None:
    """Use delta's thresholds (validate_full_config): the position's, and from slice 19
    each leg's (BotLeg.exit_delta_below/above), each between -100 and 100, below under
    above; on with none set anywhere is refused ("no delta threshold is set")."""
    position = (bot.position_delta_below, bot.position_delta_above)
    legs = [leg for leg in bot.legs
            if leg.exit_delta_below is not None or leg.exit_delta_above is not None]  # fmt: skip
    if position == (None, None) and not legs:
        return "Delta is on but nothing is set."
    in_range = lambda v: -100 <= v <= 100  # noqa: E731
    if position != (None, None):
        problem = _pair("Delta", *position, in_range, "between -100 and 100")
        if problem:
            return problem
    for leg in legs:
        problem = _pair(f"Leg {leg.position + 1}'s delta", leg.exit_delta_below,
                        leg.exit_delta_above, in_range, "between -100 and 100")  # fmt: skip
        if problem:
            return problem
    return None


def _pair(label: str, below, above, ok, words: str) -> str | None:
    if below is None and above is None:
        return f"{label} is on but nothing is set."
    if any(v is not None and not ok(v) for v in (below, above)):
        return f"{label} thresholds are {words}."
    if below is not None and above is not None and below >= above:
        return f"{label}: the 'below' threshold must be less than the 'above' one."
    return None


def _loss_action(n: int, row, pct: bool, top: int, prev: Decimal | None) -> str | None:
    """Decision 0055: an action at a negative At fires on a loss. Loss levels go down,
    each deeper than the one before (they fire in that order, on their own turn); a
    new stop sits at a bigger loss than the level, or it would fire at once."""
    if pct and row.at <= -top:
        return f"profit action {n}: a loss level is above -{top}%."
    if prev is not None and row.at >= prev:
        return "loss action levels must go down, each deeper than the one before."
    if not 0 <= row.close_pct <= 99:
        return f"profit action {n}: the close % is a whole number from 0 to 99."
    if row.close_pct == 0 and not row.moves_stop:
        return f"profit action {n} does nothing: set a close % or a stop to move to."
    if row.stop_pct is not None and row.stop_usd is not None:
        return f"profit action {n}: set the new stop in $ or %, not both."
    if row.stop_pct is not None and not -99 <= row.stop_pct <= 1000:
        return f"profit action {n}: the stop is a % of the entry price lost, -99 to 1000."
    if row.stop_pct is not None and pct and row.stop_pct <= -row.at:
        return (f"profit action {n}: its new stop must be a bigger loss than the "
                f"{-row.at:f}% it fires at, or it would fire at once.")  # fmt: skip
    if row.stop_usd is not None and not pct and row.stop_usd >= row.at:
        return f"profit action {n}: its new stop must be a bigger loss than its level."
    return None


def _profit_actions(group: GroupDef) -> str | None:
    """_exit_value_errors' profit actions, per group: only while ticked. The levels are %
    of the credit kept or gained on the debit, below 1000% (decision 0048: a credit fill
    drops what it can't reach)."""
    if not group.use_profit_actions:
        return None
    rows = group.profit_actions
    if not rows:
        return "profit actions are on: add at least one."
    if len(rows) > MAX_PROFIT_ACTIONS:
        return f"there can be at most {MAX_PROFIT_ACTIONS} profit actions."
    pct = group.target_unit != "usd"
    top = 1000
    target = group.target_value
    total, prev_at, prev_stop, prev_loss = 0, None, None, None
    for n, row in enumerate(rows, 1):
        if row.at < 0:  # decision 0055: a loss level, on its own turn
            problem = _loss_action(n, row, pct, top, prev_loss)
            if problem:
                return problem
            prev_loss, total = row.at, total + row.close_pct
            continue
        if row.at <= 0 or (pct and row.at >= top):
            return f"profit action {n}: the level is above 0" + (f" and below {top}%." if pct
                                                                 else ".")  # fmt: skip
        if prev_at is not None and row.at <= prev_at:
            return "profit action levels must go up, each above the one before."
        if target is not None and row.at >= target:
            return f"profit action {n} must be below the group's profit target."
        if not 0 <= row.close_pct <= 99:
            return f"profit action {n}: the close % is a whole number from 0 to 99."
        if row.close_pct == 0 and not row.moves_stop:
            return f"profit action {n} does nothing: set a close % or a stop to move to."
        if row.stop_pct is not None and row.stop_usd is not None:
            return f"profit action {n}: set the new stop in $ or %, not both."
        if row.stop_usd is not None and not pct and row.stop_usd >= row.at:
            # Slice 19 (_exit_value_errors): a $ P&L stop at or above the $ level the
            # action fires at would fire at once. Under a % target it's checked when the
            # action fires (bot_exits.level_problem), once the entry is known.
            return f"profit action {n}: the new stop must be below its level."
        if row.stop_pct is not None:
            if not -99 <= row.stop_pct <= 1000:
                return f"profit action {n}: the stop is a % of the entry price lost, -99 to 1000."
            if pct and -row.stop_pct >= row.at:
                return (
                    f"profit action {n}: a stop keeping {-row.stop_pct:f}% is at or above the "
                    f"{row.at:f}% it fires at, so it would fire at once."
                )
            if prev_stop is not None and row.stop_pct > prev_stop:
                return f"profit action {n}: its stop is looser than an earlier action's."
            prev_stop = row.stop_pct
        prev_at, total = row.at, total + row.close_pct
    if total >= 100:
        return "the profit actions' close % must add up to less than 100."
    return None


# Chase bounds (Tracker2's validate_execution_config has lower bounds only; the upper
# ones keep a file from setting a chase the form would never offer).
MAX_TRIES, MAX_INTERVAL = 60, 60
MAX_STEP, MAX_START_OFFSET, CHASE_GRID = Decimal("1.00"), Decimal("2.00"), Decimal("0.05")


def _chase_problem(chase, grid: Decimal = CHASE_GRID) -> str | None:
    """`grid`: the underlying's (underlyings.grid): SPX's $0.05 tick, else a cent (slice
    16, question 7)."""
    if not 1 <= chase.max_tries <= MAX_TRIES:
        return f"tries are 1 to {MAX_TRIES}."
    if not 1 <= chase.interval_seconds <= MAX_INTERVAL:
        return f"seconds between tries are 1 to {MAX_INTERVAL}."
    if not 0 < chase.step <= MAX_STEP or chase.step % grid:
        if grid == CHASE_GRID:
            return "the step is $0.05 to $1.00, in $0.05 steps (SPX's tick)."
        return f"the step is ${grid} to $1.00, in ${grid} steps."
    if not 0 <= chase.start_offset <= MAX_START_OFFSET or chase.start_offset % grid:
        return f"the start offset is $0 to $2.00, in ${grid} steps."
    return None


def _exhaust(bot: BotDef, problems: dict[str, str]) -> None:
    """Each chase's ending (validate_execution_config: a known mode, a window of
    at least a minute; 30 caps how long a close runs without you). Only the exit
    chase's is used, but a file could set either."""
    if bot.entry_chase.ignore_bidless_longs:
        problems["chase_exhaust"] = "Leaving bid-less longs to expire is for a close, not an entry."
        return
    for chase in (bot.exit_chase, bot.entry_chase):
        window = chase.window_minutes  # slice 19: entry_window_minutes, a positive integer
        if window is not None and not WINDOW_MINUTES[0] <= window <= WINDOW_MINUTES[1]:
            low, high = WINDOW_MINUTES
            problems["chase_exhaust"] = f"The entry chase's time limit is {low} to {high} minutes."
            return
        if chase.exhaust_mode not in EXHAUST_MODES:
            problems["chase_exhaust"] = f"Unknown ending for a close: {chase.exhaust_mode!r}."
            return
        if not NATURAL_MINUTES[0] <= chase.natural_minutes <= NATURAL_MINUTES[1]:
            low, high = NATURAL_MINUTES
            problems["chase_exhaust"] = f"The natural price window is {low} to {high} minutes."
            return


def _legs(bot: BotDef, problems: dict[str, str]) -> None:
    if not 1 <= len(bot.legs) <= MAX_LEGS or not 1 <= len(bot.groups) <= MAX_GROUPS:
        problems["legs"] = f"A bot has 1 to {MAX_LEGS} legs in 1 to {MAX_GROUPS} groups."
    by_position = {leg.position: leg for leg in bot.legs}
    # Bots slice 17: a futures bot's strikes always come from the Trade page's ticket
    # (analyzer_shape: "Expiry and strike always come from the tray"), so its legs are
    # a shape only and their picks aren't read.
    futures = underlyings.futures(bot.underlying)
    for leg in bot.legs:
        key = f"leg-{leg.position}"
        if leg.right not in RIGHTS or leg.pick not in PICKS:
            problems[key] = "Pick buy or sell, call or put, and how the strike is chosen."
        elif not 0 <= leg.dte <= MAX_DTE:  # before the ratio: no ratio makes it tradeable
            problems[key] = f"Days to expiration is 0 to {MAX_DTE}."
        elif not 1 <= leg.ratio <= MAX_RATIO:
            problems[key] = f"The ratio is 1 to {MAX_RATIO}."
        elif futures:
            continue
        elif leg.pick == "premium" and (leg.target is None or leg.target <= 0):
            problems[key] = "Enter the premium to aim for, above $0."
        elif leg.max_offset is not None and leg.pick != "premium":
            problems[key] = "Only a leg picked by premium can have a max offset."
        elif leg.max_offset is not None:
            problem = _linked_problem(leg, by_position.get(leg.link))
            if problem:
                problems[key] = problem
        elif leg.pick == "delta" and (
            leg.target is None or not DELTAS[0] <= leg.target <= DELTAS[1]
        ):
            problems[key] = (
                "Enter the delta to aim for: 0.01 to 0.99, or 2 to 99 in delta points "
                "(16 and 0.16 are both 16 delta)."
            )
        elif leg.pick == "spot":
            problem = _spot_problem(leg, bot.underlying)
            if problem:
                problems[key] = problem
        elif leg.pick == "offset":
            base = by_position.get(leg.link)
            if (base is None or base.pick == "offset" or base.max_offset is not None
                    or base.right != leg.right):  # fmt: skip
                problems[key] = (
                    "An offset is measured from a leg of the same type that isn't an offset "
                    "or a linked wing."
                )
            elif not _whole_points(leg.target, zero=base.dte != leg.dte):
                problems[key] = "Enter the offset in whole strike points (for example 75 or -75)."
    _expiries(bot, problems)


def _linked_problem(leg, base) -> str | None:
    """Slice 19: a bought premium leg linked to a sold one sits no further than its max
    offset from it (BotLeg.max_strike_offset; resolver._resolve_one refuses an offset on
    an unlinked leg). Whole points, pointing away from the money: below for a put,
    above for a call."""
    if leg.side is not Side.BUY:
        return "Only a bought leg can be linked to a sold leg with a max offset."
    if base is None or base.side is not Side.SELL or base.right != leg.right:
        return "A max offset is measured from a sold leg of the same type: pick it in From leg."
    if base.pick == "offset" or base.max_offset is not None:
        return "A max offset is measured from a sold leg picked on its own, not another link."
    if not _whole_points(leg.max_offset):
        return "Enter the max offset in whole strike points (for example 25 or -25)."
    if (leg.max_offset > 0) != (leg.right == "call"):
        return "A put's max offset is below its short (negative); a call's is above (positive)."
    return None


def _sizing(bot: BotDef, problems: dict[str, str]) -> None:
    """Slice 19 (validate_full_config's allocation_type): contracts or dollars, and
    dollars a positive amount of buying power. On any bot, credit or debit (decision 0050),
    never a futures bot (its size comes from the Trade page)."""
    if bot.sizing not in SIZINGS:
        problems["sizing"] = f"Unknown sizing: {bot.sizing!r}."
    elif bot.sizing == "dollars":
        # Any bot: a credit or a debit is sized by the buying power it needs (decision 0050)
        if underlyings.futures(bot.underlying):
            problems["sizing"] = (
                "A futures bot's size comes from the Trade page: size it by contracts."
            )
        elif bot.max_per_entry_usd is None or bot.max_per_entry_usd <= 0:
            problems["sizing"] = "Enter the most buying power for each entry, above $0."


def _strike_rules(bot: BotDef, problems: dict[str, str]) -> None:
    """Slice 19: the min short width is 0 or more points (Bot.min_short_width)."""
    if bot.min_short_width is not None and bot.min_short_width < 0:
        problems["strikes"] = "The min short width is 0 or more points."


def _expiries(bot: BotDef, problems: dict[str, str]) -> None:
    """Slice 14, question 3: in each group no sold leg expires after a bought leg, so
    what's left of a group once its nearer legs expire is only bought options (a
    calendar or diagonal sells the near one). A reverse calendar is refused. Tracker2
    allows any; its far leg would be a naked short with no stop of its own."""
    for group in bot.groups:
        legs = bot.legs_in(group.position)
        bought = [leg.dte for leg in legs if leg.side is Side.BUY]
        if not bought:
            continue
        for leg in legs:
            if leg.side is Side.SELL and leg.dte > min(bought):
                problems.setdefault(
                    f"leg-{leg.position}",
                    "A sold leg can't expire after a bought leg of its group (a reverse "
                    "calendar). Put it in its own group, or give it the nearer DTE.",
                )


DELTAS = (Decimal("0.01"), Decimal("0.99"))  # a delta target, as a magnitude


def _whole_points(value: Decimal | None, zero: bool = False) -> bool:
    """Whole strike points; 0 only when `zero` (slice 14: a calendar's legs in two
    expirations share a strike; in one expiration that's the same option twice)."""
    return value is not None and (zero or value != 0) and value == value.to_integral_value()


def _spot_problem(leg, underlying: str = "SPX") -> str | None:
    """Whole points from the underlying's price, 0 being at the money (a straddle or a fly
    body); a short points out of the money (question 7: Tracker2 allows an in-the-money
    offset short, for diagonals)."""
    if leg.target is None or leg.target != leg.target.to_integral_value():
        return (
            f"Enter the points from the {underlying} price, whole points (for example -50 or 50)."
        )
    if leg.side is Side.SELL and leg.right == "put" and leg.target > 0:
        return "A short put's points are below the price: a negative number, like -50."
    if leg.side is Side.SELL and leg.right == "call" and leg.target < 0:
        return "A short call's points are above the price: a positive number, like 50."
    return None


def _groups(bot: BotDef, problems: dict[str, str]) -> None:
    known = {group.position for group in bot.groups}
    if any(leg.group not in known for leg in bot.legs):
        problems.setdefault("legs", "Every leg belongs to a group.")
    names: set[str] = set()
    for group in bot.groups:
        key = f"group-{group.position}"
        if not bot.legs_in(group.position):
            problems[key] = "This group has no legs."
            continue
        if group.name in names:
            problems[key] = "Two groups can't share a name."
            continue
        names.add(group.name)
        kind = "single_short" if _one_sold(bot, group.position) else "watched"
        problem = _stop_problem(group, kind) or _target_problem(group)
        if problem is None and group.broker_exit not in BROKER_EXITS:
            problem = "Pick whether the stop or the profit target rests at the broker."
        # The remainder setting is for a group with one short; a watched stop always
        # closes the whole group, so what's left there is ignored, not refused.
        left = len(bot.legs_in(group.position)) - 1  # the legs left after its stop
        problem = problem or (_remainder_problem(group, left) if kind == "single_short" else None)
        if problem:
            problems[key] = problem


PAIR_RESTING = (
    "Only a stop resting at the broker can close another group when it fills: this group's "
    "stop is watched by the app (it has no single sold leg, or its profit target rests)."
)


def _pairing(bot: BotDef, problems: dict[str, str]) -> None:
    """When a group's stop fills, also close another group (bots slice 21; Tracker2's
    on_stop_close_group_id, remainder._resolve_paired_target): another group of this bot,
    never itself, never a loop; and only on a group whose stop rests at the broker, since
    only that stop's fill starts it (question 9: not a watched group, not Schwab's watched
    stops, not a group with no stop)."""
    known = {g.position: g for g in bot.groups}
    for group in bot.groups:
        target = group.close_on_stop
        if target is None:
            continue
        key = f"group-{group.position}"
        if target == group.position:
            problems.setdefault(key, "A group can't close itself after its stop.")
        elif target not in known:
            problems.setdefault(key, "The group to close after the stop doesn't exist.")
        elif group.stop_value is None:
            problems.setdefault(key, "This group has no stop, so nothing would close the other.")
        elif not _one_sold(bot, group.position) or group.broker_exit == "target":
            problems.setdefault(key, PAIR_RESTING)
        else:
            seen, node = {group.position}, known.get(target)
            while node is not None and node.close_on_stop is not None:
                if node.close_on_stop in seen or node.position in seen:
                    problems.setdefault(key, "These groups close each other in a loop.")
                    break
                seen.add(node.position)
                node = known.get(node.close_on_stop)


def _stop_problem(group: GroupDef, kind: str) -> str | None:
    """validate_stop_loss_basis and _exit_value_errors, for one group's stop. Which
    styles a group can use depends on its kind: a group with one sold leg takes any
    (decision 0048: some only on a credit fill, exit_plan.read_against_fill); any other only
    % of entry price lost or $ (decision 0039)."""
    if group.stop_unit not in EXIT_UNITS or group.stop_basis not in STOP_BASES:
        return "Pick how the stop is measured."
    value = group.stop_value
    if value is None:
        return None
    if group.stop_unit == "usd":
        return None if value > 0 else "A $ stop is the loss per contract, above $0."
    if kind == "watched" and group.stop_basis not in WATCHED_BASES:
        return (
            "This group has no single short to rest a stop on. Use $ loss per contract or "
            "% of entry price lost."
        )
    if group.stop_basis == "closing_order":
        if value > 0:
            return None
        return "A closing-order stop is a dollar amount off the entry credit, above $0."
    if 1 <= value <= 1000:
        return None
    if group.stop_basis == "group_entry":
        return "The stop is a % of the entry price lost, between 1 and 1000."
    if group.stop_basis == "entry_premium":
        return "The stop is a % of the entry credit, between 1 and 1000."
    return "The stop is a % above the short's fill, between 1 and 1000."


def _target_problem(group: GroupDef) -> str | None:
    """The target is the % of the credit kept or gained on the debit (1 to 1000), read
    against the fill (decision 0048)."""
    if group.target_unit not in EXIT_UNITS:
        return "Pick % or $ for the profit target."
    value = group.target_value
    if value is None:
        return None
    if group.target_unit == "usd":
        return None if value > 0 else "A $ profit target is the profit per contract, above $0."
    if 1 <= value <= 1000:
        return None
    return (
        "The profit target is the % of the credit kept or gained on the debit, between 1 and 1000."
    )


ONE_LIMIT = (
    "A limit price works only when one leg is left after the stop; leave it blank to use "
    "each leg's bid."
)


def _remainder_problem(group: GroupDef, left: int) -> str | None:
    """validate_on_stop_remainder and validate_on_stop_remainder_limit. A typed limit
    is one price for one leg: refused when the stop leaves more than one (`left`)."""
    if group.on_stop_remainder not in REMAINDERS:
        return "Pick what happens to the group's other legs after a stop."
    if group.remainder_limit is not None and group.remainder_limit <= 0:
        return "The limit price must be above $0, or blank to use the bid."
    if group.remainder_limit is not None and left > 1:
        return ONE_LIMIT
    return None


def _closing_order(bot: BotDef, problems: dict[str, str]) -> None:
    """validate_stop_loss_co: a known mode, and an amount above $0 while it's on. On
    a debit fill it is dropped when the trade opens (decision 0048, exit_plan)."""
    if bot.stop_co_mode not in CO_MODES:
        problems["closing_order"] = "Pick Off, Every group or When tighter for the closing order."
    elif bot.stop_co_mode != "off" and (bot.stop_co_amount is None or bot.stop_co_amount <= 0):
        problems["closing_order"] = "The closing order is on: set its dollar amount, above $0."


def _times(bot: BotDef, problems: dict[str, str]) -> None:
    seen = set()
    for entry in bot.entry_times:
        text = entry.time
        found = ENTRY_TIME.match(text)
        if not found:
            problems["times"] = f"{text!r} isn't a time like 09:47 or 09:30:15."
            return
        if found.group(3) and int(found.group(3)) % SECOND_STEP:
            problems["times"] = f"{text}: seconds go in 15-second steps, like 09:30:15."
            return
        at = clock(text)
        if not FIRST_ENTRY <= at < LAST_ENTRY:
            problems["times"] = "Entry times are from 09:30 to 15:57 (New York)."
            return
        if text in seen:
            problems["times"] = f"{text} is listed twice."
            return
        seen.add(text)
        if entry.quantity is not None and not 1 <= entry.quantity <= MAX_CONTRACTS:
            problems["times"] = (
                f"entry time {text}: quantity must be 1 to {MAX_CONTRACTS}, "
                "or blank for the bot's contracts"
            )
            return
        ratio = largest_ratio(bot)
        if entry.quantity is not None and entry.quantity * ratio > day_cap(bot) >= 1:
            # Never sendable. Over max_per_trade alone is fine: it's cut down when it runs.
            counted = (
                f"{entry.quantity} contracts"
                if ratio == 1
                else f"{entry.quantity} contracts × the largest leg's ratio ({ratio})"
            )
            problems["times"] = (
                f"entry time {text}: {counted} is more than the daily limit of {day_cap(bot)}"
            )
            return
        if entry.label is not None and len(entry.label) > 50:
            problems["times"] = f"entry time {text}: the label can be up to 50 characters."
            return
    if underlyings.futures(bot.underlying):
        return  # bots slice 17: a futures bot never fires its entry times (_futures)
    if bot.per_trade:
        return  # manual bots (#158): a per-trade bot manages one trade and never enters
    if not bot.use_floating_entry and not any(entry.enabled for entry in bot.entry_times):
        problems["times"] = "Add at least one entry time that's on."


def _caps(bot: BotDef, problems: dict[str, str]) -> None:
    for cap in (bot.max_entries_per_day, bot.max_open_trades):
        if cap is not None and cap < 1:
            problems["caps"] = "Caps are blank (no cap) or a whole number, 1 or more."


def _premium(bot: BotDef, problems: dict[str, str]) -> None:
    """The bounds are checked only while on (decision 0048: on the size of the price, either
    side)."""
    if not bot.use_premium_bounds:
        return
    low, high = bot.premium_min, bot.premium_max
    if low is None and high is None:
        problems["premium"] = "Premium bounds is on: set a minimum, a maximum, or both."
    elif (low is not None and low <= 0) or (high is not None and high <= 0):
        problems["premium"] = "Premium bounds must be above $0."
    elif low is not None and high is not None and low > high:
        problems["premium"] = "The minimum premium can't be more than the maximum."


def _frequency(bot: BotDef, problems: dict[str, str]) -> None:
    if bot.frequency not in FREQUENCIES:
        problems["frequency"] = "Pick daily, weekly, monthly or specific dates."
    elif bot.frequency == "weekly":
        if not bot.entry_days or any(not 1 <= day <= 7 for day in bot.entry_days):
            problems["frequency"] = "Weekly needs at least one day ticked."
    elif bot.frequency == "monthly":
        week, weekday = bot.monthly_week, bot.monthly_weekday
        if week is None or weekday is None or not 1 <= week <= 5 or not 1 <= weekday <= 5:
            problems["frequency"] = "Monthly needs a week (First–Fifth) and a weekday (Mon–Fri)."
    elif bot.frequency == "specific_dates":
        if not bot.specific_dates and not bot.specific_date_sets:
            problems["frequency"] = "Specific dates needs at least one date or list."
        elif not _known_sets(bot.specific_date_sets, SPECIFIC_SETS):
            problems["frequency"] = _SPECIFIC_SET
        elif not all(is_date(day) for day in bot.specific_dates):
            problems["frequency"] = (
                "Every specific date must be a real date like 2026-10-14. Not dates: "
                + ", ".join(day for day in bot.specific_dates if not is_date(day))
                + "."
            )


def _blackout(bot: BotDef, problems: dict[str, str]) -> None:
    if not bot.use_blackout_days:
        return
    if not bot.blackout_days and not bot.blackout_date_sets and not bot.blackout_dte_sets:
        problems["blackout"] = "Blackout days is on but no date or list is set."
    elif not _known_sets((*bot.blackout_date_sets, *bot.blackout_dte_sets)):
        problems["blackout"] = _UNKNOWN_SET
    elif not _known_sets(bot.blackout_date_sets, ENTRY_DAY_SETS):
        problems["blackout"] = "A market holiday has no session: tick it Before expiration only."
    elif not all(is_date(day) for day in bot.blackout_days):
        problems["blackout"] = (
            "Every blackout day must be a real date like 2026-12-25. Not dates: "
            + ", ".join(day for day in bot.blackout_days if not is_date(day))
            + "."
        )


_UNKNOWN_SET = f"Date lists are {', '.join(DATE_SETS)}."
_SPECIFIC_SET = f"Specific dates lists are {', '.join(SPECIFIC_SETS)}."


def _known_sets(names, allowed=DATE_SETS) -> bool:
    """_validate_date_sets: every ticked list is one the app knows, and allows there."""
    return all(name in allowed for name in names)


def _market_filters(bot: BotDef, problems: dict[str, str]) -> None:
    """Tracker2's validate_vix_level, validate_vix_overnight, validate_vix9d_ratio,
    validate_technicals and validate_intraday_move: each checked only while its box
    is ticked, and a box ticked with nothing set is refused."""
    checks = (
        ("vix_level", bot.use_vix_level, lambda: _bounds("VIX level", bot.vix_min, bot.vix_max)),
        ("vix_overnight", bot.use_vix_overnight,
         lambda: _moves("VIX overnight move", bot.vix_overnight)),
        ("vix9d_ratio", bot.use_vix9d_ratio,
         lambda: _bounds("VIX9D/VIX ratio", bot.vix9d_ratio_min, bot.vix9d_ratio_max)),
        ("technicals", bot.use_technicals, lambda: _technicals(bot)),
        ("intraday_move", bot.use_intraday_move,
         lambda: _moves("Intraday movement", bot.intraday)),
    )  # fmt: skip
    for key, on, check in checks:
        problem = check() if on else None
        if problem:
            problems[key] = problem


def _bounds(label: str, low, high) -> str | None:
    if low is None and high is None:
        return f"{label} is on: set a minimum, a maximum, or both."
    if (low is not None and low <= 0) or (high is not None and high <= 0):
        return f"{label}: each limit must be above 0."
    if low is not None and high is not None and low > high:
        return f"{label}: the minimum can't be more than the maximum."
    return None


def _moves(label: str, limits: MoveLimits) -> str | None:
    """_move_limit_errors: each % 0 or more, a min not above its max, one set."""
    if not limits.any_set:
        return f"{label} is on: set at least one of min up, max up, min down, max down."
    values = (limits.min_up, limits.max_up, limits.min_down, limits.max_down)
    if any(v is not None and v < 0 for v in values):
        return f"{label}: use positive numbers (each is a % move)."
    for low, high, way in ((limits.min_up, limits.max_up, "up"),
                           (limits.min_down, limits.max_down, "down")):  # fmt: skip
        if low is not None and high is not None and low > high:
            return f"{label} {way}: the minimum can't be more than the maximum."
    return None


# (name, unit, Above/Below smallest period, largest), technicals._AVERAGES
_AVERAGES = {"SMA": ("days", 2, 200), "EMA": ("minutes", 1, 390)}


def _technicals(bot: BotDef) -> str | None:
    """validate_technicals. SMA(1) is the price itself (Option Omega's SMA includes
    it), so Above/Below needs at least 2 days; Compare needs two different periods."""
    averages = (("SMA", bot.sma_mode, bot.sma_days_a, bot.sma_days_b),
                ("EMA", bot.ema_mode, bot.ema_minutes_a, bot.ema_minutes_b))  # fmt: skip
    if not any(mode for _, mode, _, _ in averages):
        return "Technical indicators is on but neither SMA nor EMA is set."
    for name, mode, a, b in averages:
        if not mode:
            continue
        unit, single_low, high = _AVERAGES[name]
        if mode not in MODES:
            return f"{name}: pick none, below, above or compare."
        if mode == "compare":
            if not (_whole(a, 1, high) and _whole(b, 1, high)):
                return f"{name} compare: two periods, each 1 to {high} {unit}."
            if a == b:
                return f"{name} compare: the two periods must differ."
        elif not _whole(a, single_low, high):
            return f"{name} {mode}: the period is {single_low} to {high} {unit}."
    return None


def _whole(n, low: int, high: int) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and low <= n <= high
