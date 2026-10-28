"""A bot as a shareable JSON file, and back (spec: bots slice 8, section 5;
Tracker2's share_format.py and share_import.parse_upload). Pure.

Only fields named in SHARED go out, and every BotDef field must be in SHARED or
PRIVATE (tests/bots/test_bot_file.py), so a new setting stays private until
someone decides. A file never holds an account, credentials, sizes, tags or
anything about runs (decision 0005). Reading is strict: a file comes from a
stranger, so every value is type-checked, unknown keys are ignored, and any
problem is a FileError with a message for the user, never a crash.
"""

import json
import re
from dataclasses import MISSING, fields, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from optionsjourney.bots import definition
from optionsjourney.bots.definition import (
    DEFAULT_ENTRY_CHASE,
    DEFAULT_EXIT_CHASE,
    BotDef,
    DefaultAlertDef,
    EntryTimeDef,
    GroupDef,
    LegDef,
    MoveLimits,
    ProfitActionDef,
    TimeActionDef,
    lone_shorts_rest_stops,
    normalize_delta,
)
from optionsjourney.trading.orders import Side

FORMAT = "optionsjourney.bot"
VERSION = 3  # 3: no opens_for, each side's "rests" (decision 0048); 1 and 2 still import
MAX_BYTES = 256 * 1024  # share_import.MAX_BYTES
MAX_LEGS, MAX_SIDES, MAX_TIMES = definition.MAX_LEGS, definition.MAX_GROUPS, 50  # the editor's
_ET = ZoneInfo("America/New_York")

# Never written, never read: yours, not the bot's design.
PRIVATE = frozenset({
    "id", "tag_id", "broker", "account_number", "live", "enabled", "contracts",
    "max_per_trade", "max_per_day", "tags", "archived",
    "per_trade",  # manual bots (#158): a hidden copy for one trade, never exported
    "alert_name", "alert_channel",  # share_format.PRIVATE_FIELDS: they name your setup
    # slice 19: sizes, as build_snapshot writes a fixed-value bot as quantity, no allocation
    "sizing", "max_per_entry_usd",
})  # fmt: skip
ENTRY_TIME_SHARED = frozenset({"time", "enabled", "label"})  # never its quantity


class FileError(ValueError):
    """A file that can't be imported; the message is for the user."""


# --- What each field is ----------------------------------------------------------------
# Kinds: text, text? (or null), int, int?, bool, money (a number as text), money?,
# ints, texts. Each child list has its own table; the defaults are the dataclass's.

_MOVES = {name: "money?" for name in ("min_up", "max_up", "min_down", "max_down")}
_LEG = {
    "position": "int",
    "side": "text",
    "right": "text",
    "ratio": "int",
    "dte": "int",
    "pick": "text",
    "target": "money?",
    "link": "int?",
    "exact": "bool",
    "group": "int",
    "exit_delta_below": "money?",  # slice 19
    "exit_delta_above": "money?",
    "max_offset": "money?",
    "fallback_target": "money?",  # #314
}
_SIDE = {
    "position": "int",
    "name": "text",
    "stop_pct": "money?",
    "target_pct": "money?",
    "stop_basis": "text",
    "stop_unit": "text",
    "stop_usd": "money?",
    "target_unit": "text",
    "target_usd": "money?",
    "on_stop_remainder": "text",
    "remainder_limit": "money?",
    "use_profit_actions": "bool",
    "profit_actions": "profits",
    "close_on_stop": "int?",  # slice 21: when its stop fills, also close that group
    "broker_exit": "text",  # decision 0048, "rests" in the file
}
_SIDE_KEY = {"broker_exit": "rests"}
_PROFIT = {"at": "money", "close_pct": "int", "stop_pct": "money?", "stop_usd": "money?"}
_ALERT = {"type": "text", "direction": "text?", "value": "money?", "points": "money?"}
_TIME = {"time": "text", "enabled": "bool", "label": "text?"}
_CHASE = {
    "start_offset": "money",
    "step": "money",
    "interval_seconds": "int",
    "max_tries": "int",
    "exhaust_mode": "text",
    "natural_minutes": "int",
    "window_minutes": "int?",  # slice 19
    "switch_to_market": "bool",
    "ignore_bidless_longs": "bool",  # decision 0065
}
_ACTION = {
    "type": "text",
    "days": "int",
    "at": "text",
    "stop_pct": "money?",
    "target_pct": "money?",
    "close_pct": "int?",
}
_BOT = {
    "name": "text", "underlying": "text", "split_iron_condors": "bool",
    "max_entries_per_day": "int?", "max_open_trades": "int?",
    "use_premium_bounds": "bool", "premium_min": "money?",
    "premium_max": "money?", "frequency": "text", "entry_days": "ints", "monthly_week": "int?",
    "monthly_weekday": "int?", "specific_dates": "texts", "use_blackout_days": "bool",
    "blackout_days": "texts", "stop_co_mode": "text", "stop_co_amount": "money?",
    "use_vix_level": "bool", "vix_min": "money?", "vix_max": "money?",
    "use_vix_overnight": "bool", "vix_overnight": "moves",
    "use_vix9d_ratio": "bool", "vix9d_ratio_min": "money?", "vix9d_ratio_max": "money?",
    "use_technicals": "bool", "sma_mode": "text?", "sma_days_a": "int?", "sma_days_b": "int?",
    "ema_mode": "text?", "ema_minutes_a": "int?", "ema_minutes_b": "int?",
    "use_intraday_move": "bool", "intraday": "moves",
    "specific_date_sets": "texts", "blackout_date_sets": "texts",
    "blackout_dte_sets": "texts",  # #207: missing (a file from before) is none
    "close_time": "text?", "close_dte": "int", "use_time_actions": "bool",
    "time_actions": "actions", "legs": "legs", "groups": "sides", "entry_times": "times",
    "entry_chase": "chase", "exit_chase": "chase",
    "use_delta_exit": "bool", "position_delta_below": "money?", "position_delta_above": "money?",
    "use_sl_ratio_exit": "bool", "sl_ratio_below": "money?", "sl_ratio_above": "money?",
    "use_underlying_exit": "bool", "underlying_up_pct": "money?", "underlying_down_pct": "money?",
    "short_put_tested_pts": "money?", "short_call_tested_pts": "money?",
    "use_default_alerts": "bool", "default_alerts": "alerts",
    "default_alerts_in_app_only": "bool",
    "use_floating_entry": "bool", "floating_start": "text?", "floating_end": "text?",
    "use_exact_dte": "bool",  # slice 14: missing is on, a new bot's default
    "min_short_width": "money?", "move_conflicted_strikes": "bool",  # slice 19
    "keep_trying_minutes": "int?",  # #314
    "use_min_short_width": "bool", "use_keep_trying": "bool",  # #315: missing is on
}  # fmt: skip
SHARED = frozenset(_BOT)
# The file says "sides", like the app's pages.
_FILE_KEY = {"groups": "sides"}
_LIMIT = 10**9  # no whole number or amount in a bot comes near this


# --- Writing ---------------------------------------------------------------------------


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:f}"


def _out(kind: str, value):
    if kind in ("money", "money?"):
        return _money(value)
    if kind in ("ints", "texts"):
        return list(value)
    if kind == "moves":
        return {name: _money(getattr(value, name)) for name in _MOVES}
    if kind == "legs":
        return [_row(leg, _LEG) | {"side": str(leg.side)} for leg in value]
    if kind == "sides":
        return [_row(group, _SIDE, _SIDE_KEY) for group in value]
    if kind == "times":
        return [_row(entry, _TIME) for entry in value]
    if kind == "chase":
        return _row(value, _CHASE)
    if kind == "actions":
        return [_row(action, _ACTION) for action in value]
    if kind == "profits":
        return [_row(action, _PROFIT) for action in value]
    if kind == "alerts":
        return [_row(rule, _ALERT) for rule in value]
    return value


def _row(obj, table: dict, keys: dict | None = None) -> dict:
    return {
        (keys or {}).get(name, name): _out(kind, getattr(obj, name)) for name, kind in table.items()
    }


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def to_file(bot: BotDef, now: datetime) -> dict:
    """The shareable file of `bot` as of `now` (build_snapshot): the allowlisted
    definition, with blackout days already past dropped (and blackout days off if
    nothing is left to black out, as the validator would refuse it on)."""
    data = {
        _FILE_KEY.get(name, name): _out(kind, getattr(bot, name)) for name, kind in _BOT.items()
    }
    today = now.astimezone(_ET).date().isoformat()
    data["blackout_days"] = [day for day in data["blackout_days"] if day >= today]
    if not (data["blackout_days"] or data["blackout_date_sets"] or data["blackout_dte_sets"]):
        data["use_blackout_days"] = False
    return {"format": FORMAT, "version": VERSION, "exported_at": _iso(now), "bot": data}


def dumps(bot: BotDef, now: datetime) -> str:
    return json.dumps(to_file(bot, now), indent=2) + "\n"


def file_name(name: str) -> str:
    """The bot's name in kebab-case: "SPX IC (copy 2)" -> spx-ic-copy-2.json."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{slug or 'bot'}.json"


# --- Reading ---------------------------------------------------------------------------


class Imported:
    """A file's bot (no account, tag or sizes yet) and when it was exported.
    `source`: "optionsjourney", or "tracker2" for a Tracker2 file, whose `notes` say
    what couldn't come over as it was (decision 0057)."""

    def __init__(self, bot: BotDef, exported_at: datetime | None,
                 source: str = "optionsjourney", notes: tuple[str, ...] = ()):  # fmt: skip
        self.bot, self.exported_at = bot, exported_at
        self.source, self.notes = source, notes


def parse(raw: bytes | str) -> Imported:
    """A bot from a file's bytes or pasted text (parse_upload)."""
    if len(raw) > MAX_BYTES:
        raise FileError("This file is too large to be a bot file.")
    try:
        data = json.loads(raw)
    except ValueError, UnicodeDecodeError, RecursionError:
        raise FileError("This isn't a bot file: it isn't JSON.") from None
    return from_file(data)


def from_file(data) -> Imported:
    """validate_snapshot, _check_shapes and payload_from_snapshot in one: the
    format, the version, then every shared field typed. Unknown keys (an account,
    "live", anything) are ignored. Missing settings take a new bot's defaults. A
    Tracker2 file is translated to this format first (decision 0057)."""
    from optionsjourney.bots import tracker2_file  # it builds on this module

    if tracker2_file.is_tracker2(data):
        translated, notes = tracker2_file.translate(data)
        imported = from_file(translated)
        imported.source, imported.notes = "tracker2", notes
        return imported
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise FileError("This isn't an OptionsJourney bot file.")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise FileError("This bot file has no version number, so it can't be read.")
    if version > VERSION:
        raise FileError(
            f"This bot was saved by a newer OptionsJourney (file version {version}). "
            "Update the app to import it."
        )
    body = data.get("bot")
    if not isinstance(body, dict):
        raise FileError("This file has no bot in it.")
    if not isinstance(body.get("name"), str) or not body["name"].strip():
        raise FileError("This file has no bot in it: the bot has no name.")
    values = {}
    for name, kind in _BOT.items():
        key = _FILE_KEY.get(name, name)
        if key in body:
            values[name] = _read(kind, body[key], key)
    if version < 3 and "groups" in values:
        # Decision 0048: what a version 1 or 2 bot opened for sets each group's Rests at the
        # broker (version 1 only made credit iron condors), then is ignored
        opens = "credit" if version == 1 else body.get("opens_for")
        rests = "target" if opens == "debit" else "stop"
        values["groups"] = tuple(replace(g, broker_exit=rests) for g in values["groups"])
    _check_size(values)
    if "legs" in values:  # a delta reads 16 and 0.16 alike, as on the form
        values["legs"] = tuple(
            replace(leg, target=normalize_delta(leg.target)) if leg.pick == "delta" else leg
            for leg in values["legs"]
        )
    bot = replace(BotDef(None, "", None, "tastytrade", ""), **values)
    # decision 0054: a group of more than one leg rests its target, whatever the file says
    return Imported(lone_shorts_rest_stops(bot), _exported_at(data.get("exported_at")))


def _check_size(values: dict) -> None:
    for name, most, what in (("legs", MAX_LEGS, "legs"), ("groups", MAX_SIDES, "sides"),
                             ("entry_times", MAX_TIMES, "entry times")):  # fmt: skip
        if len(values.get(name, ())) > most:
            raise FileError(f"This bot has more than {most} {what}; this app's bots have up to "
                            f"{most}. Update the app, or ask whoever shared it.")  # fmt: skip


def _exported_at(raw) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.removesuffix("Z")).replace(tzinfo=UTC)
    except AttributeError, TypeError, ValueError:
        return None


def _wrong(where: str, what: str) -> FileError:
    return FileError(f"This bot file can't be read: {where} {what}.")


def _read(kind: str, value, where: str):
    """One value of `kind` from the file, or FileError naming `where`."""
    optional = kind.endswith("?")
    base = kind.rstrip("?")
    if value is None and optional:
        return None
    if base == "text":
        if not isinstance(value, str):
            raise _wrong(f"'{where}'", "isn't text")
        return value
    if base == "int":
        if not isinstance(value, int) or isinstance(value, bool) or abs(value) >= _LIMIT:
            raise _wrong(f"'{where}'", "isn't a whole number in range")
        return value
    if base == "bool":
        if not isinstance(value, bool):
            raise _wrong(f"'{where}'", "isn't true or false")
        return value
    if base == "money":
        return _decimal(value, where)
    if base in ("ints", "texts"):
        if not isinstance(value, list):
            raise _wrong(f"'{where}'", "isn't a list")
        return tuple(_read(base[:-1], item, where) for item in value)
    if base == "moves":
        return MoveLimits(**_object(value, _MOVES, where, MoveLimits, f"'{where}'"))
    if base == "chase":
        default = DEFAULT_ENTRY_CHASE if where == "entry_chase" else DEFAULT_EXIT_CHASE
        return replace(default, **_object(value, _CHASE, where, None, f"'{where}'"))
    if base == "legs":
        legs = tuple(_child(LegDef, item, _LEG, f"leg {n}", where)
                     for n, item in enumerate(_list(value, where), 1))  # fmt: skip
        return _in_order(legs, "legs")
    if base == "sides":
        sides = tuple(_child(GroupDef, item, _SIDE, f"side {n}", where, _SIDE_KEY)
                      for n, item in enumerate(_list(value, where), 1))  # fmt: skip
        for n, side in enumerate(sides, 1):
            if len(side.name) > 60:
                raise _wrong(f"side {n}'s 'name'", "is longer than 60 characters")
        return _in_order(sides, "sides")
    if base == "times":
        return tuple(_child(EntryTimeDef, item, _TIME, f"entry time {n}", where)
                     for n, item in enumerate(_list(value, where), 1))  # fmt: skip
    if base == "actions":
        return tuple(_child(TimeActionDef, item, _ACTION, f"time action {n}", where)
                     for n, item in enumerate(_list(value, where), 1))  # fmt: skip
    if base == "profits":
        return tuple(_child(ProfitActionDef, item, _PROFIT, f"profit action {n}", where)
                     for n, item in enumerate(_list(value, where), 1))  # fmt: skip
    if base == "alerts":
        return tuple(_child(DefaultAlertDef, item, _ALERT, f"default alert {n}", where)
                     for n, item in enumerate(_list(value, where), 1))  # fmt: skip
    raise AssertionError(kind)  # pragma: no cover -- every kind is handled above


def _decimal(value, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise _wrong(f"'{where}'", "isn't a number")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise _wrong(f"'{where}'", "isn't a number") from None
    if not number.is_finite() or abs(number) >= _LIMIT or number.as_tuple().exponent < -6:
        raise _wrong(f"'{where}'", "isn't a number in range")
    return number


def _list(value, where: str) -> list:
    if not isinstance(value, list):
        raise _wrong(f"'{where}'", "isn't a list")
    return value


def _child(cls, value, table: dict, where: str, container: str, keys: dict | None = None):
    """One leg, side, entry time or time action ("leg 2" in 'legs')."""
    return cls(**_object(value, table, where, cls, f"{where} in '{container}'", keys))


def _object(value, table: dict, where: str, cls, label: str, keys: dict | None = None) -> dict:
    """The fields of one object, typed. A field with no default in `cls` is
    required; unknown keys are ignored."""
    if not isinstance(value, dict):
        raise _wrong(label, "isn't an object")
    required = set()
    if cls is not None:
        required = {f.name for f in fields(cls) if f.name in table and _no_default(f)}
    out = {}
    for name, kind in table.items():
        key = (keys or {}).get(name, name)
        if key in value:
            out[name] = _read(kind, value[key], f"{where}'s {key}")
        elif name in required:
            raise _wrong(where, f"has no '{key}'")
    if "side" in out:
        try:
            out["side"] = Side(out["side"])
        except ValueError:
            raise _wrong(f"{where}'s 'side'", "must be buy or sell") from None
    return out


def _no_default(field) -> bool:
    return field.default is MISSING and field.default_factory is MISSING


def _in_order(items: tuple, what: str) -> tuple:
    """Legs and sides refer to each other by position: 0, 1, 2 ... in order."""
    if [item.position for item in items] != list(range(len(items))):
        raise _wrong(f"'{what}'", "aren't numbered 0, 1, 2 ... in order (their 'position')")
    return items
