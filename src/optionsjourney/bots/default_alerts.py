"""A bot's default alerts → the alerts to make when an entry fills (spec: bots
slice 9, section 4; Tracker2's trade_alerts/defaults.py:build_specs). Pure.

- P&L % → one alert on what the whole trade costs to close (its legs' mids, sold
  legs counted minus, per unit), against the entry's credit.
- Near a short strike → one SPX price alert per distinct sold strike: a put at K
  goes off below K + points, a call above K − points.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from optionsjourney.bots.definition import DefaultAlertDef

MIN_ENTRY_DOLLARS = Decimal(50)  # Tracker2's PCT_MIN_NET_ENTRY
MULTIPLIER = 100
MAX_NAME = 60  # defaults.MAX_BOT_NAME_IN_ALERT
_RIGHTS = {"put": ("P", "put", "below"), "call": ("C", "call", "above")}


@dataclass(frozen=True)
class AlertSpec:
    key: str  # the rule, unique per entry: "pnl_pct:above:15", "strike:P:5700:10"
    kind: str  # "bot_pl" (the trade's close cost) or "price" (SPX)
    target: str  # what it reads: "SPX", or the run for bot_pl
    name: str
    direction: str  # "above" or "below"
    level: Decimal
    legs: list = field(default_factory=list)  # bot_pl: [{"key": symbol, "qty": signed}]
    note: str = ""


def plain(value: Decimal) -> str:
    """5700 → "5700", 10.50 → "10.5" (defaults._plain)."""
    text = f"{Decimal(value).normalize():f}"
    return text


def _short_name(name: str) -> str:
    return name if len(name) <= MAX_NAME else name[: MAX_NAME - 1] + "…"


def _pct(value: Decimal) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}{plain(abs(value))}%"


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pnl(rule, prefix, credit, contracts, legs) -> tuple[AlertSpec | None, str | None]:
    key = f"pnl_pct:{rule.direction}:{plain(rule.value)}"
    if credit is None or abs(credit) * MULTIPLIER * max(contracts, 1) < MIN_ENTRY_DOLLARS:
        return None, f"{key}: the entry took in less than $50, too little to measure a % on"
    pct = rule.value / 100
    if credit > 0:  # a credit: the close cost falls as the trade makes money
        level = credit * (1 - pct)
        direction = "below" if rule.direction == "above" else "above"
    else:  # a debit: the trade's value rises as it makes money
        level = -credit * (1 + pct)
        direction = rule.direction
    level = _money(level)
    if level <= 0:
        return None, f"{key}: a close price of {level} can't be reached"
    word = "≥" if rule.direction == "above" else "≤"
    side = "credit" if credit > 0 else "debit"
    return AlertSpec(
        key, "bot_pl", "", f"{prefix} · P&L {word} {_pct(rule.value)}", direction, level,
        [{"key": leg["symbol"], "qty": _signed(leg)} for leg in legs],
        f"entry {abs(credit):.2f} {side}",
    ), None  # fmt: skip


def _signed(leg: dict) -> int:
    """A leg's contracts per unit, sold ones counted minus (a spread alert's qty)."""
    return (-1 if leg["side"] == "sell" else 1) * int(leg.get("ratio", 1))


def _shorts(legs) -> list[tuple[str, Decimal]]:
    """The distinct (right, strike) of the sold legs, puts first, then by strike."""
    seen = {(leg["right"], Decimal(str(leg["strike"]))) for leg in legs
            if leg.get("side") == "sell" and leg.get("right") in _RIGHTS}  # fmt: skip
    return sorted(seen, key=lambda rk: (rk[0] != "put", rk[1]))


def _crossed(direction: str, level: Decimal, spot: Decimal | None) -> bool:
    """Would it go off at once? Strict, as the app's alerts compare."""
    if spot is None:
        return False
    return spot < level if direction == "below" else spot > level


def specs(
    bot_name: str,
    root_id: int,
    rules,
    credit: Decimal | None,
    contracts: int,
    legs: list[dict],
    spot: Decimal | None,
    underlying: str = "SPX",
) -> tuple[list[AlertSpec], list[str]]:
    """The alerts for an entry, and why any rule made none."""
    prefix = f"{_short_name(bot_name)} · run {root_id}"
    out, skipped = [], []
    for rule in rules or ():
        if not isinstance(rule, DefaultAlertDef):
            continue
        if rule.type == "pnl_pct" and rule.value is not None:
            spec, why = _pnl(rule, prefix, credit, contracts, legs)
            if spec is not None:
                out.append(spec)
            else:
                skipped.append(why)
        elif rule.type == "strike_offset" and rule.points is not None:
            for right, strike in _shorts(legs):
                code, word, direction = _RIGHTS[right]
                level = strike + rule.points if direction == "below" else strike - rule.points
                key = f"strike:{code}:{plain(strike)}:{plain(rule.points)}"
                if _crossed(direction, level, spot):
                    skipped.append(f"{key}: {underlying} is already at {spot}")
                    continue
                where = (f"near {word} {plain(strike)} ({plain(rule.points)} pts)"
                         if rule.points else f"at {word} {plain(strike)}")  # fmt: skip
                out.append(AlertSpec(key, "price", underlying, f"{prefix} · {where}",
                                     direction, level))  # fmt: skip
    return out, skipped
