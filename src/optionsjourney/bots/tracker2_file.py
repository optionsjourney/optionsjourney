"""A Tracker2 bot file (its share_format.build_snapshot, "tracker2.bot" schema 1) as
this app's own bot file, with a note for each setting that can't come over as it
was (decision 0057). Pure.

The translation only renames and reshapes: bot_file.from_file then reads the result
as strictly as any file, so every value is still type-checked there, and only the
fields this app shares come in (never sizes, the account, the alert name or tags).
"""

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import reduce

from optionsjourney.bots import bot_file
from optionsjourney.bots.bot_file import FileError
from optionsjourney.bots.definition import group_kind
from optionsjourney.bots.manual import MAX_ALERTS, ManualRules, read_form

FORMAT = "tracker2.bot"
SCHEMA_VERSION = 1

# Same name, same meaning (bot_file._BOT's keys that Tracker2's Bot has too)
_SAME = (
    "underlying", "frequency", "entry_days", "monthly_week", "monthly_weekday",
    "specific_dates", "use_blackout_days", "blackout_days", "specific_date_sets",
    "blackout_date_sets", "blackout_dte_sets", "max_entries_per_day", "use_premium_bounds",
    "premium_min", "premium_max", "use_vix_level", "vix_min", "vix_max", "use_vix_overnight",
    "use_vix9d_ratio", "vix9d_ratio_min", "vix9d_ratio_max", "use_technicals", "sma_mode",
    "sma_days_a", "sma_days_b", "ema_mode", "ema_minutes_a", "ema_minutes_b",
    "use_intraday_move", "use_time_actions", "use_delta_exit", "position_delta_below",
    "position_delta_above", "use_sl_ratio_exit", "sl_ratio_below", "sl_ratio_above",
    "use_underlying_exit", "underlying_up_pct", "underlying_down_pct", "use_default_alerts",
    "default_alerts", "default_alerts_in_app_only", "use_floating_entry", "floating_start",
    "floating_end", "use_exact_dte", "move_conflicted_strikes",
)  # fmt: skip
_RENAMED = {
    "max_open_positions": "max_open_trades",
    "stop_loss_co_mode": "stop_co_mode",
    "stop_loss_co_amount": "stop_co_amount",
    "scheduled_close_time": "close_time",
}
_MOVES = {"min_up": "min_up_pct", "max_up": "max_up_pct",
          "min_down": "min_down_pct", "max_down": "max_down_pct"}  # fmt: skip
_PICKS = {"fixed_premium": "premium", "parent_offset": "offset", "delta": "delta",
          "spot_offset": "spot"}  # fmt: skip
_CHASE = {
    "starting_offset": "start_offset",
    "price_increment": "step",
    "interval_seconds": "interval_seconds",
    "max_attempts": "max_tries",
    "exhaust_mode": "exhaust_mode",
    "natural_minutes": "natural_minutes",
    "entry_window_minutes": "window_minutes",
    "switch_to_market_after_max_attempts": "switch_to_market",
    "ignore_bidless_longs": "ignore_bidless_longs",
}
# Tracker2's chase endings this app leaves out (bots slice 19), and what they become
_ENDINGS = {"leave_alert": "Leave working and alert"}
_LISTS = ("legs", "leg_groups", "entry_times", "time_actions", "default_alerts",
          "profit_actions")  # fmt: skip


def is_tracker2(data) -> bool:
    return isinstance(data, dict) and data.get("format") == FORMAT


def is_analyzer(data) -> bool:
    """A Tracker2 bot that enters from the Analyzer: it has no entry times or schedule
    here, so it comes over as a manual bot (#158), not a bot."""
    bot = data.get("bot") if is_tracker2(data) else None
    return isinstance(bot, dict) and bot.get("entry_source") == "analyzer"


def _shared_bot(data: dict) -> dict:
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise FileError("This bot file has no version number, so it can't be read.")
    if version > SCHEMA_VERSION:
        raise FileError(f"This bot file is from a newer version of the app that made it "
                        f"(schema {version}); this app reads schema {SCHEMA_VERSION}.")  # fmt: skip
    t = data.get("bot")
    if not isinstance(t, dict):
        raise FileError("This file has no bot in it.")
    return t


def _alert_rows(t: dict) -> list[dict]:
    """The default alerts Tracker2 uses: none when it has them switched off."""
    rows = t.get("default_alerts") if t.get("use_default_alerts") else None
    if rows is None:
        return []
    if not (isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
        raise FileError("This bot file can't be read: 'default_alerts' isn't a list "
                        "of objects.")  # fmt: skip
    return rows[:MAX_ALERTS]


def is_place_only(data: dict) -> bool:
    """An analyzer bot with no profit target, no stop loss, no scheduled close and no
    default alerts in use:
    Tracker2 calls it "Place only". The Trade page's Manage with has that built in."""
    t = _shared_bot(data)
    return (t.get("entry_source") == "analyzer"
            and t.get("profit_target_pct") is None and t.get("profit_target_usd") is None
            and t.get("stop_loss_pct") is None and t.get("stop_loss_usd") is None
            and not t.get("scheduled_close_time")
            and t.get("scheduled_close_dte") is None and not _alert_rows(t))  # fmt: skip


def _amount(t: dict, kind: str) -> tuple[str, str]:
    unit = t.get(f"{kind}_unit") or "pct"
    value = t.get(f"{kind}_{unit}")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
        raise FileError(f"This bot file can't be read: its {kind} isn't a number.")
    return "" if value is None else str(value), unit


def manual_of(data: dict) -> tuple[ManualRules | None, tuple[str, ...]]:
    """A Tracker2 analyzer bot as manual-bot rules (its target and stop, each in its own
    unit, and its scheduled close), and a note for each thing left behind. None for the
    rules when it is Place only: nothing to save."""
    t = _shared_bot(data)
    if is_place_only(data):
        return None, ()
    target, target_unit = _amount(t, "profit_target")
    stop, stop_unit = _amount(t, "stop_loss")
    close_dte = t.get("scheduled_close_dte")
    if close_dte is not None and (isinstance(close_dte, bool) or not isinstance(close_dte, int)):
        raise FileError("This bot file can't be read: its close day isn't a number.")
    close_time = t.get("scheduled_close_time")
    if close_time is not None and not isinstance(close_time, str):
        raise FileError("This bot file can't be read: its close time isn't a time.")
    name = t.get("name")
    form = {"name": name.strip()[:100] if isinstance(name, str) else "",
            "target_value": target, "target_unit": target_unit,
            "stop_value": stop, "stop_unit": stop_unit,
            "close_time": close_time or "", "close_dte": str(close_dte or 0)}  # fmt: skip
    alerts = _alert_rows(t)
    form["alerts"] = str(len(alerts))
    for i, row in enumerate(alerts):
        kind = row.get("type") or "pnl_pct"
        form[f"da-{i}-type"] = str(kind)
        form[f"da-{i}-direction"] = str(row.get("direction") or "above")
        form[f"da-{i}-value"] = "" if row.get("value") is None else str(row["value"])
        form[f"da-{i}-points"] = "" if row.get("points") is None else str(row["points"])
    rules, problems = read_form(form)
    if problems:
        raise FileError("This bot file can't be read: " + " ".join(problems.values()))
    notes = []
    if t.get("legs"):
        notes.append("Its legs aren't kept: a manual bot only sets exits, and works on the "
                     "legs of the trade you attach it to.")  # fmt: skip
    if t.get("execution_configs"):
        notes.append("Its chase settings aren't kept: a manual bot never opens a trade.")
    notes.append("Its account and size aren't kept: you pick the trade a manual bot manages.")
    if t.get("underlying"):
        notes.append("Its ticker isn't kept: a manual bot works on any trade.")
    if t.get("default_alerts_in_app_only") and rules.alerts:
        notes.append("The app it came from kept this bot's alerts in that app only; here they "
                     "go wherever alerts go.")  # fmt: skip
    return rules, tuple(notes)


def translate(data: dict) -> tuple[dict, tuple[str, ...]]:
    """This app's bot file for a Tracker2 one, and the notes on what changed on the way."""
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise FileError("This bot file has no version number, so it can't be read.")
    if version > SCHEMA_VERSION:
        raise FileError(f"This bot file is from a newer version of the app that made it "
                        f"(schema {version}); this app reads schema {SCHEMA_VERSION}.")  # fmt: skip
    t = data.get("bot")
    if not isinstance(t, dict):
        raise FileError("This file has no bot in it.")
    _check_shapes(t)
    notes: list[str] = []
    body = {key: t[key] for key in _SAME if key in t}
    body |= {ours: t[theirs] for theirs, ours in _RENAMED.items() if theirs in t}
    body["name"] = t.get("name")
    _holiday_before_expiration(body)
    if t.get("scheduled_close_dte") is not None:
        body["close_dte"] = t["scheduled_close_dte"]
    if t.get("min_short_width") not in (None, 0):  # 0 is off in Tracker2
        body["min_short_width"] = t["min_short_width"]
    body["vix_overnight"] = {ours: t.get(f"vix_on_{theirs}") for ours, theirs in _MOVES.items()}
    body["intraday"] = {ours: t.get(f"intraday_{theirs}") for ours, theirs in _MOVES.items()}
    for right in ("put", "call"):  # a row that's off is blank here
        if t.get(f"use_short_{right}_tested"):
            body[f"short_{right}_tested_pts"] = t.get(f"short_{right}_tested_pts")
    body["entry_times"] = [
        {"time": row.get("entry_time"), "enabled": row.get("is_enabled", True),
         "label": row.get("label")}
        for row in t.get("entry_times") or []
    ]  # fmt: skip
    body["legs"], body["sides"] = _legs_and_sides(t, notes)
    _dollars_per_unit(body["legs"], body["sides"], notes)
    body["time_actions"] = [_time_action(row) for row in t.get("time_actions") or []]
    if any(row.get("adjust_sl_pct") is not None for row in t.get("time_actions") or []):
        notes.append("A time action that moves the stop sets it in each group's own stop "
                     "style here; the app it came from reads it as a % loss of the entry. "
                     "They're the same for a group of one sold leg.")  # fmt: skip
    for phase in ("entry", "exit"):
        config = (t.get("execution_configs") or {}).get(phase)
        if config is not None:
            body[f"{phase}_chase"] = _chase(phase, config, notes)
    if t.get("exit_mode") not in (None, "hybrid"):
        notes.append(
            f"Its exit mode in the app it came from ({t['exit_mode']}) isn't a setting here: "
            "each "
            "group rests one exit at the broker and the app watches the other."
        )
    if t.get("entry_source") == "analyzer":
        notes.append("The app it came from entered this bot by hand; here it enters at its "
                     "entry times.")  # fmt: skip
    out = {"format": bot_file.FORMAT, "version": bot_file.VERSION,
           "exported_at": data.get("updated_at") or data.get("shared_at"),
           "bot": _whole_numbers(body)}  # fmt: skip
    return out, tuple(notes)


def _holiday_before_expiration(body: dict) -> None:
    """A market holiday saved as an entry-day list (Tracker2's form before ef862660) is,
    as Tracker2 reads it (guards.blackout_dte_names), a Before expiration one (#207)."""
    entry, later = body.get("blackout_date_sets"), body.get("blackout_dte_sets") or []
    if not isinstance(entry, list) or "HOLIDAY" not in entry or not isinstance(later, list):
        return
    body["blackout_date_sets"] = [name for name in entry if name != "HOLIDAY"]
    body["blackout_dte_sets"] = sorted({*later, "HOLIDAY"}, key=str)


def _check_shapes(t: dict) -> None:
    """The containers walked here must be what they claim (share_format._check_shapes):
    a file from a stranger mustn't crash the import. Values are checked by bot_file."""
    for key in _LISTS:
        rows = t.get(key)
        if rows is not None and not (isinstance(rows, list)
                                     and all(isinstance(r, dict) for r in rows)):  # fmt: skip
            raise FileError(f"This bot file can't be read: '{key}' isn't a list of "
                            "objects.")  # fmt: skip
    for row in t.get("leg_groups") or []:
        if not isinstance(row.get("profit_actions") or [], list) or not all(
            isinstance(r, dict) for r in row.get("profit_actions") or []
        ):
            raise FileError("This bot file can't be read: a group's 'profit_actions' "
                            "isn't a list of objects.")  # fmt: skip
    configs = t.get("execution_configs")
    if configs is not None and not (
        isinstance(configs, dict) and all(isinstance(c, dict) for c in configs.values())
    ):
        raise FileError("This bot file can't be read: 'execution_configs' isn't "
                        "an object of objects.")  # fmt: skip


def _legs_and_sides(t: dict, notes: list[str]) -> tuple[list, list]:
    grouped = bool(t.get("use_leg_groups")) and bool(t.get("leg_groups"))
    rows = t.get("legs") or []
    legs = [
        {"position": n, "side": row.get("action"), "right": row.get("option_type"),
         "ratio": row.get("quantity", 1), "dte": row.get("dte") or 0,
         "pick": _PICKS.get(row.get("selection_mode") or "fixed_premium",
                            row.get("selection_mode")),
         "target": row.get("target_value"), "link": row.get("linked_leg_index"),
         "exact": row.get("exact_strike_offset", True),
         "group": row.get("group_index") if grouped else 0,
         "exit_delta_below": row.get("exit_delta_below"),
         "exit_delta_above": row.get("exit_delta_above"),
         "max_offset": row.get("max_strike_offset")}
        for n, row in enumerate(rows)
    ]  # fmt: skip
    debit = t.get("premium_side") == "debit"
    if not grouped:
        sides = _whole_trade(t, rows, debit)
        notes.append("The app it came from traded this bot as one whole entry; here its legs "
                     "are one group, Whole trade, with the bot's stop and target.")  # fmt: skip
        return legs, [sides]
    sides = []
    for n, row in enumerate(t["leg_groups"]):
        actions = [leg.get("action") for leg in rows if leg.get("group_index") == n]
        side = _side(n, row.get("name") or f"Group {n + 1}", row, debit)
        side |= {"on_stop_remainder": row.get("on_stop_remainder") or "close_market",
                 "remainder_limit": row.get("on_stop_remainder_limit"),
                 "close_on_stop": row.get("on_stop_close_group_index")}  # fmt: skip
        if not _one_short(actions) and side["stop_basis"] != "group_entry":
            if side["stop_pct"] is not None or side["stop_usd"] is not None:
                notes.append(
                    f"{side['name']}: its stop is a % of the group's entry here: "
                    "the app watches it, as the group isn't one sold leg."
                )
            side["stop_basis"] = "group_entry"
        sides.append(side)
    return legs, sides


def _dollars_per_unit(legs: list, sides: list, notes: list[str]) -> None:
    """A group's $ amounts at this app's unit (#218). Tracker2 prices them per the full
    ratio (exits.usd_stop_trigger divides by the leg's quantity, group_entry_net
    multiplies by it); here a unit is the ratios over their gcd (exit_math.group_legs,
    decision 0084). So each $ stop, $ target, $ profit-action level and profit-action $
    stop is divided by the group's gcd, to the cent: a $2.00 stop on a x3 short is
    $0.67 a contract, as it was in Tracker2. A % is the same either way."""
    for side in sides:
        ratios = [leg["ratio"] for leg in legs if leg["group"] == side["position"]]
        if not all(isinstance(r, int) and not isinstance(r, bool) and r > 0 for r in ratios):
            continue  # bot_file refuses the leg itself
        gcd = reduce(math.gcd, ratios, 0)
        if gcd <= 1:
            continue
        changed = False
        for key in ("stop_usd", "target_usd"):
            changed |= _divide(side, key, gcd)
        for action in side["profit_actions"]:
            if side["target_unit"] == "usd":
                changed |= _divide(action, "at", gcd)
            changed |= _divide(action, "stop_usd", gcd)
        if changed:
            notes.append(f"{side['name']}: the app it came from sets its $ amounts per {gcd} "
                         f"sets of its legs and this app per one set, so they're ÷ {gcd} "
                         "here, to the cent.")  # fmt: skip


def _divide(row: dict, key: str, by: int) -> bool:
    """row[key] / by to the cent, as text; False when there's no number to divide (a
    value that isn't one is left for bot_file to refuse)."""
    value = row.get(key)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return False
    if not number.is_finite():
        return False
    row[key] = f"{(number / by).quantize(Decimal('0.01'), ROUND_HALF_UP):f}"
    return True


def _whole_trade(t: dict, rows: list, debit: bool) -> dict:
    side = _side(0, "Whole trade", t, debit)
    one_short = _one_short(row.get("action") for row in rows)
    side["stop_basis"] = "leg_premium" if one_short and not debit else "group_entry"
    return side


def _side(position: int, name: str, row: dict, debit: bool) -> dict:
    """A group's (or a whole-entry bot's) exits."""
    rests = row.get("broker_exit") or ("target" if debit else "stop")
    return {
        "position": position, "name": name,
        "stop_pct": row.get("stop_loss_pct"), "target_pct": row.get("profit_target_pct"),
        "stop_basis": row.get("stop_loss_basis") or "leg_premium",
        "stop_unit": row.get("stop_loss_unit") or "pct", "stop_usd": row.get("stop_loss_usd"),
        "target_unit": row.get("profit_target_unit") or "pct",
        "target_usd": row.get("profit_target_usd"),
        "use_profit_actions": bool(row.get("use_profit_actions")),
        "profit_actions": [
            {"at": a.get("at"), "close_pct": a.get("close_pct") or 0,
             "stop_pct": a.get("adjust_sl_pct"), "stop_usd": a.get("adjust_sl_usd")}
            for a in row.get("profit_actions") or []
        ],
        "rests": rests,
    }  # fmt: skip


def _one_short(actions) -> bool:
    try:
        return group_kind(actions) == "single_short"
    except ValueError:  # not buy or sell: bot_file refuses the leg itself
        return False


def _time_action(row: dict) -> dict:
    return {"type": row.get("type"), "days": row.get("days"), "at": row.get("at"),
            "stop_pct": row.get("adjust_sl_pct"), "target_pct": row.get("adjust_pt_pct"),
            "close_pct": row.get("close_pct")}  # fmt: skip


def _chase(phase: str, config: dict, notes: list[str]) -> dict:
    out = {ours: config[theirs] for theirs, ours in _CHASE.items()
           if config.get(theirs) is not None or theirs == "entry_window_minutes"}  # fmt: skip
    ending = config.get("exhaust_mode")
    if ending in _ENDINGS:
        out["exhaust_mode"] = "give_up"
        notes.append(
            f"The {phase} chase's ending, {_ENDINGS[ending]}, isn't in this app: "
            "after its last try it gives up (a close puts the stop back)."
        )
    return out


def _whole_numbers(value):
    """Tracker2 writes some whole numbers as floats (50.0); bot_file wants them whole.
    Amounts read the same either way."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_whole_numbers(v) for v in value]
    if isinstance(value, dict):
        return {k: _whole_numbers(v) for k, v in value.items()}
    return value
