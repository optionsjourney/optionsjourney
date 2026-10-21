"""Manual bots (#158): named exit rules for any open trade, read from the form. Pure.

A manual bot holds only a profit target and a stop loss (each % of the trade's net
credit/debit, or dollars; both optional), an exit time (days before expiration and a
New York HH:MM; off by default) and alerts (a bot's default alert rules). A manual bot
with none of the exits is allowed: it only alerts.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from optionsjourney.bots.definition import DefaultAlertDef
from optionsjourney.bots.schedule import normalize_time

UNITS = ("pct", "usd")
MAX_NAME = 100  # the column's size
MAX_ALERTS = 20  # as a bot's default alerts


@dataclass(frozen=True)
class ManualRules:
    name: str
    target_value: Decimal | None = None
    target_unit: str = "pct"  # "pct" (of the trade's net credit/debit) or "usd"
    stop_value: Decimal | None = None
    stop_unit: str = "pct"
    close_time: str | None = None  # HH:MM New York; None: no exit time
    close_dte: int = 0  # days before (the nearest) expiration
    alerts: tuple[DefaultAlertDef, ...] = ()


def has_exits(rules: ManualRules) -> bool:
    """Whether the rules can close the trade (else they only alert)."""
    return rules.target_value is not None or rules.stop_value is not None or bool(rules.close_time)


def _text(form: Mapping, key: str) -> str:
    return str(form.get(key) or "").strip()


def _decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _amount(form, key, label, problems) -> Decimal | None:
    raw = _text(form, f"{key}_value")
    if not raw:
        return None
    value = _decimal(raw)
    if value is None:
        problems[f"{key}_value"] = f"{label}: {raw!r} isn't a number."
    elif value <= 0:
        problems[f"{key}_value"] = f"{label} must be more than 0."
    return value


def _unit(form, key, label, problems) -> str:
    unit = _text(form, f"{key}_unit") or "pct"
    if unit not in UNITS:
        problems[f"{key}_unit"] = f"{label}: choose % or $."
    return unit


def _alerts(form: Mapping, problems: dict[str, str]) -> tuple[DefaultAlertDef, ...]:
    """The alert rows typed in, read like a bot's default alerts (da-N-type,
    -direction, -value, -points); a row without its kind's number is dropped."""
    try:
        count = min(int(_text(form, "alerts") or 0), MAX_ALERTS)
    except ValueError:
        count = 0
    rules = []
    for i in range(count):
        kind = _text(form, f"da-{i}-type") or "pnl_pct"
        raw = _text(form, f"da-{i}-value" if kind == "pnl_pct" else f"da-{i}-points")
        if not raw:
            continue
        value = _decimal(raw)
        if kind not in ("pnl_pct", "strike_offset"):
            problems.setdefault("alerts", f"Alert {len(rules) + 1}: unknown kind {kind!r}.")
        elif value is None:
            problems.setdefault("alerts", f"Alert {len(rules) + 1}: {raw!r} isn't a number.")
        elif kind == "pnl_pct":
            direction = _text(form, f"da-{i}-direction") or "above"
            if direction not in ("above", "below"):
                problems.setdefault("alerts", f"Alert {len(rules) + 1}: above or below?")
            rules.append(DefaultAlertDef(kind, direction, value))
            continue
        elif value < 0:
            problems.setdefault("alerts", f"Alert {len(rules) + 1}: points can't be below 0.")
        else:
            rules.append(DefaultAlertDef(kind, points=value))
            continue
        rules.append(DefaultAlertDef(kind))
    return tuple(rules)


def read_form(form: Mapping) -> tuple[ManualRules, dict[str, str]]:
    """The manual bot form → its rules and the problems found, keyed by field."""
    problems: dict[str, str] = {}
    name = _text(form, "name")
    if not name:
        problems["name"] = "Give the manual bot a name."
    elif len(name) > MAX_NAME:
        problems["name"] = f"Keep the name to {MAX_NAME} characters."
    target = _amount(form, "target", "Profit target", problems)
    target_unit = _unit(form, "target", "Profit target", problems)
    stop = _amount(form, "stop", "Stop loss", problems)
    stop_unit = _unit(form, "stop", "Stop loss", problems)
    raw_time = _text(form, "close_time")
    close_time = normalize_time(raw_time) if raw_time else None
    if raw_time and close_time is None:
        problems["close_time"] = f"Exit time: {raw_time!r} isn't a time (HH:MM, New York)."
    raw_dte = _text(form, "close_dte") or "0"
    close_dte = int(raw_dte) if raw_dte.isdigit() else 0
    if not raw_dte.isdigit():
        problems["close_dte"] = "Days before expiration: a whole number, 0 or more."
    rules = ManualRules(name, target, target_unit, stop, stop_unit, close_time, close_dte,
                        _alerts(form, problems))  # fmt: skip
    return rules, problems
