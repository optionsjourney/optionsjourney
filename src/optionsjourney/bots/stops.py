"""A side's stop trigger and profit-target level (Tracker2's exits._place_stop_loss,
_co_overlay_trigger, usd_stop_trigger, group_usd_close_price and stop_calc.py).
Pure.

Three stop styles read the same amount (Tracker2 reuses stop_loss_pct):
- leg_premium: % above the short's fill: fill x (1 + pct/100)
- entry_premium: % of the whole entry credit: credit x pct/100, with no "1 +"
- closing_order: dollars off the whole entry credit: credit - amount
A $ stop (stop_unit "usd") is the short's fill + the dollars; it skips the styles
and the overlay. The closing-order overlay ("both" or "tighter") is one dollar
amount off the entry credit; "tighter" compares raw numbers and a tie keeps the
side's own. The trigger is rounded once, at the end, down to the short's tick."""

from dataclasses import dataclass
from decimal import Decimal

from optionsjourney.bots.definition import GroupDef
from optionsjourney.bots.exit_math import round_stop

OVERLAY_ON = ("both", "tighter")


def leg_trigger(short_fill: Decimal, pct: Decimal) -> Decimal:
    """exits.stop_trigger_price: a % lost on the short, added on."""
    return short_fill * (1 + pct / 100)


def entry_trigger(entry_credit: Decimal, pct: Decimal) -> Decimal:
    """exits.entry_basis_trigger: the % IS the buy-back price. 95% of 5.00 is 4.75."""
    return entry_credit * pct / 100


def closing_order_trigger(entry_credit: Decimal, amount: Decimal) -> Decimal:
    """exits.closing_order_trigger: 5.00 with a 0.20 closing order is 4.80."""
    return entry_credit - amount


def usd_trigger(short_fill: Decimal, usd: Decimal, ratio: int = 1) -> Decimal:
    """exits.usd_stop_trigger for a single short at P&L level -usd. A single-leg order
    is priced per contract, so a ratio-2 anchor divides the dollars by 2."""
    return short_fill + usd / ratio


def _text(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:f}"


@dataclass(frozen=True)
class StopCalc:
    """How a side's trigger was worked out (stop_calc.freeze). `problem` set:
    the stop must not be sent."""

    basis: str  # leg_premium, entry_premium, closing_order, usd, level, or (watched)
    # group_usd, group_entry, group_level: there `trigger` is the signed P&L level per
    # unit, to the cent, and `tick` is None
    configured: Decimal
    short_fill: Decimal | None  # None on a watched level
    entry_credit: Decimal | None
    configured_raw: Decimal | None  # the side's own trigger, before the overlay
    co_mode: str
    co_amount: Decimal | None
    co_raw: Decimal | None
    co_won: bool
    raw: Decimal | None
    tick: Decimal | None
    trigger: Decimal | None
    problem: str | None = None
    group_entry: Decimal | None = None  # entry_lost: the group's own entry per unit

    def as_log(self) -> dict:
        """Every number as text, for the stop's JSON log."""
        log = {
            "stop_calc": {
                "basis": self.basis,
                "configured": _text(self.configured),
                "short_fill": _text(self.short_fill),
                "entry_credit": _text(self.entry_credit),
                "configured_raw": _text(self.configured_raw),
                "co_mode": self.co_mode,
                "co_amount": _text(self.co_amount),
                "co_raw": _text(self.co_raw),
                "winner": "co" if self.co_won else "basis",
                "raw": _text(self.raw),
                "tick": _text(self.tick),
                "trigger": _text(self.trigger),
                "problem": self.problem,
            }
        }
        if self.group_entry is not None:  # only entry_lost: other logs stay as they were
            log["stop_calc"]["group_entry"] = _text(self.group_entry)
        return log


def stop_calc(
    group: GroupDef,
    co_mode: str,
    co_amount: Decimal | None,
    short_symbol: str,
    short_fill: Decimal,
    entry_credit: Decimal | None,
    ratio: int = 1,
    group_entry: Decimal | None = None,
) -> StopCalc | None:
    """The side's stop, or None if it has none. `entry_credit` is the whole
    trade's credit per unit: its fill, else its order price
    (exits._entry_credit_for_basis). `group_entry`: the group's own entry per unit, for
    the group_entry style on a short (decision 0048: the % of the group's entry lost,
    counting the wing as worthless, as level_calc does; "entry_lost" in the log)."""
    value = group.stop_value
    if value is None:
        return None
    if group.stop_unit == "usd":
        raw = usd_trigger(short_fill, value, ratio)
        tick, trigger = round_stop(short_symbol, raw)
        problem = None if trigger > 0 else f"the $ stop works out at {trigger:f}, not above 0"
        return StopCalc(
            "usd", value, short_fill, None, raw, "off", None, None, False, raw, tick, trigger,
            problem,
        )  # fmt: skip
    basis = group.stop_basis
    overlay = co_mode in OVERLAY_ON and co_amount is not None
    if basis == "group_entry" and group_entry is None:
        return StopCalc(
            "entry_lost", value, short_fill, None, None, co_mode, co_amount, None, False, None,
            None, None, "the group has no entry price to base the stop on",
        )  # fmt: skip
    if (basis in ("entry_premium", "closing_order") or overlay) and entry_credit is None:
        return StopCalc(
            basis, value, short_fill, None, None, co_mode, co_amount, None, False, None, None,
            None, "the run has no entry credit to base the stop on",
        )  # fmt: skip
    if basis == "closing_order":
        configured = closing_order_trigger(entry_credit, value) / ratio
    elif basis == "entry_premium":
        configured = entry_trigger(entry_credit, value) / ratio
    elif basis == "group_entry":
        configured = group_entry * (1 + value / 100) / ratio
    else:
        configured = leg_trigger(short_fill, value)
    raw, co_raw, co_won = configured, None, False
    if overlay:
        co_raw = closing_order_trigger(entry_credit, co_amount) / ratio
        if co_mode == "both" or co_raw < configured:  # strictly lower: a tie keeps its own
            raw, co_won = co_raw, True
    tick, trigger = round_stop(short_symbol, raw)
    problem = None
    if (basis != "leg_premium" or co_won) and trigger <= 0:
        problem = (
            f"the stop works out at {trigger:f}: the amount is at or above the entry "
            f"credit {'?' if entry_credit is None else format(entry_credit, 'f')}"
        )
    elif trigger <= short_fill:
        problem = (
            f"the stop works out at {trigger:f}, at or below the short's fill "
            f"{short_fill:f}, so it would fire at once"
        )
    lost = basis == "group_entry"
    return StopCalc(
        "entry_lost" if lost else basis, value, short_fill, entry_credit, configured, co_mode,
        co_amount, co_raw, co_won, raw, tick, trigger, problem, group_entry if lost else None,
    )  # fmt: skip


def level_calc(
    short_symbol: str,
    short_fill: Decimal,
    entry: Decimal,
    loss_pct: Decimal,
    ratio: int = 1,
) -> StopCalc:
    """A profit action's stop (bots slice 7; Tracker2's profit_actions.stop_level_for_pct
    on a single short): the P&L level `loss_pct` % of the entry lost (0 is
    break-even, -25 keeps a quarter of it), as a trigger on the short, counting the
    wing as worthless: credit x (1 + loss_pct/100). Never "below the short's fill"
    (a stop moved after a profit is meant to be); the caller checks it against the
    live ask instead. A problem when it works out at or below 0."""
    raw = entry * (1 + loss_pct / 100) / ratio  # priced per contract of a ratio anchor
    tick, trigger = round_stop(short_symbol, raw)
    problem = None if trigger > 0 else f"the stop works out at {trigger:f}, not above 0"
    return StopCalc(
        "level", loss_pct, short_fill, entry, raw, "off", None, None, False, raw, tick,
        trigger, problem,
    )  # fmt: skip


def level_usd_calc(
    short_symbol: str, short_fill: Decimal, entry: Decimal, level: Decimal, ratio: int = 1
) -> StopCalc:
    """A profit action's stop at a $ P&L level (bots slice 19; Tracker2's adjust_sl_usd,
    "$ P&L"): `level` per contract in option price, + keeping that much profit, as a
    trigger on the short counting the wing as worthless: entry - level, per contract of a
    ratio anchor (level_calc's % twin). A problem when it works out at or below 0; the
    caller checks it against the live ask (level_problem)."""
    raw = (entry - level) / ratio
    tick, trigger = round_stop(short_symbol, raw)
    problem = None if trigger > 0 else f"the stop works out at {trigger:f}, not above 0"
    return StopCalc(
        "level_usd", level, short_fill, entry, raw, "off", None, None, False, raw, tick,
        trigger, problem,
    )  # fmt: skip


CENT = Decimal("0.01")


def _watched(
    basis: str, configured: Decimal, entry: Decimal, level: Decimal | None, problem: str | None
) -> StopCalc:
    return StopCalc(
        basis, configured, None, entry, level, "off", None, None, False, level, None, level,
        problem,
    )  # fmt: skip


def watched_calc(group: GroupDef, entry: Decimal) -> StopCalc | None:
    """The stop of a group the app watches (spec section 3; Tracker2's engine-watched $
    stop): a P&L level per unit, `-usd`, or `-|entry| x pct`, to the cent. No broker
    order rests, so there's no tick and no short. A problem when the level isn't below
    0 (it would fire at once) or a % has no entry to measure."""
    value = group.stop_value
    if value is None:
        return None
    if group.stop_unit == "usd":
        return _watched_level_calc("group_usd", value, entry, -value)
    if entry == 0:
        return _watched(
            "group_entry", value, entry, None, "the group's entry is 0, so a % of it is 0"
        )
    return _watched_level_calc("group_entry", value, entry, -abs(entry) * value / 100)


def _watched_level_calc(basis: str, configured: Decimal, entry: Decimal, raw: Decimal) -> StopCalc:
    level = raw.quantize(CENT)
    problem = None if level < 0 else f"the stop works out at P&L {level:f}, not below 0"
    return _watched(basis, configured, entry, level, problem)


def watched_level(entry: Decimal, loss_pct: Decimal, why: str) -> StopCalc:
    """A watched group's stop moved by a profit action (the watched twin of level_calc):
    `loss_pct` % of the group's entry lost (0 is break-even, -25 keeps a quarter of it),
    as a P&L level per unit. A moved stop may sit at or above 0, so this only refuses an
    entry of 0. `why` names what moved it, for the log."""
    if entry == 0:
        return _watched("group_level", loss_pct, entry, None, f"{why}: the group's entry is 0")
    return _watched(
        "group_level", loss_pct, entry, (-abs(entry) * loss_pct / 100).quantize(CENT), None
    )


def watched_level_usd(entry: Decimal, level: Decimal, why: str) -> StopCalc:
    """A watched group's stop moved to a $ P&L level by a profit action (bots slice 19):
    the level itself, to the cent (watched_level's $ twin)."""
    return _watched("group_level_usd", level, entry, level.quantize(CENT), None)


def profit_move(
    entry: Decimal, pct: Decimal | None, why: str, *, anchor: str | None = None,
    anchor_fill: Decimal | None = None, ratio: int = 1, usd: Decimal | None = None,
) -> StopCalc:  # fmt: skip
    """A profit action's new stop (scale_out: stop_level_for_pct): on the group's short
    `anchor` (level_calc, or level_usd_calc with `usd`), else a P&L level the app watches
    (watched_level, watched_level_usd). The one choice bot_exits.profit_stop_calc and the
    paper watcher make."""
    if anchor is None:
        if usd is not None:
            return watched_level_usd(entry, usd, why)
        return watched_level(entry, pct, why)
    if usd is not None:
        return level_usd_calc(anchor, anchor_fill, entry, usd, ratio)
    return level_calc(anchor, anchor_fill, entry, pct, ratio=ratio)


def action_move(
    group: GroupDef | None, pct: Decimal | None, entry: Decimal | None,
    entry_credit: Decimal | None, why: str, *, anchor: str | None = None,
    anchor_fill: Decimal | None = None, ratio: int = 1, usd: Decimal | None = None,
) -> StopCalc:  # fmt: skip
    """A stop moved to `pct` (a time action, or you): on the short `anchor` in the group's
    own % style (a $ or closing-order group uses % above the short's fill), without the
    closing-order overlay; with no anchor, a P&L level on the group's `entry`. `usd`: the
    loss per contract instead (stops.usd_trigger, or a watched level at -$). The one
    choice bot_exits._moved_calc and the paper watcher make."""
    dollars = None if usd is None else GroupDef(0, "", stop_unit="usd", stop_usd=usd)
    if anchor is None:
        return (
            watched_calc(dollars, entry) if dollars is not None else watched_level(entry, pct, why)
        )
    own = group is not None and group.stop_unit == "pct"
    basis = group.stop_basis if own and group.stop_basis != "closing_order" else "leg_premium"
    moved = dollars or GroupDef(0, "", stop_pct=pct, stop_basis=basis)
    return stop_calc(moved, "off", None, anchor, anchor_fill, entry_credit, ratio=ratio)


def target_limit(group: GroupDef, entry: Decimal) -> tuple[Decimal | None, str | None]:
    """(the group's close value to aim at, a problem), signed like the entry: the
    entry less the profit, where profit is the $ amount or the % of |entry|
    (exits.group_usd_close_price, profit_target_price). A credit that can never be
    reached (limit at or below 0) is refused; a debit entry closes for a credit."""
    value = group.target_value
    if value is None:
        return None, None
    profit = value if group.target_unit == "usd" else abs(entry) * value / 100
    limit = entry - profit
    if entry >= 0 and limit <= 0:
        return limit, (f"the profit target {value:f} is at or above the group's credit {entry:f}")
    return limit, None


def calc_of(log) -> dict | None:
    """The stop arithmetic in force, the latest kept in an exit's log (a time action
    that moved the stop adds one), if any."""
    return next(
        (
            e["stop_calc"]
            for e in reversed(list(log or ()))
            if isinstance(e, dict) and "stop_calc" in e
        ),
        None,
    )


def _money(text: str) -> str:
    """At least two decimals, more only when the number has them (stop_calc._money)."""
    d = Decimal(text).normalize()
    if d.as_tuple().exponent > -2:
        d = d.quantize(Decimal("0.01"))
    return f"{d:f}"


def _pct(text: str) -> str:
    d = Decimal(text).normalize()
    return f"{d.quantize(Decimal(1)) if d == d.to_integral() else d:f}"


def _describe_watched(calc: dict) -> list[str]:
    entry = Decimal(calc["entry_credit"])
    kind = "debit" if entry < 0 else "credit"
    level = Decimal(calc["trigger"])
    sign = "−" if level < 0 else "+" if level > 0 else ""
    head = f"P&L {sign}{_money(str(abs(level)))} a contract: "
    basis, amount = calc["basis"], calc["configured"]
    if basis == "group_usd":
        text = f"${_money(amount)} stop"
    elif basis == "group_level_usd":
        text = "$ P&L level"
    elif basis == "group_entry":
        text = f"entry {_money(str(abs(entry)))} {kind} × {_pct(amount)}%"
    else:
        pct = Decimal(amount)
        share = f"{_pct(str(-pct))}% kept" if pct < 0 else f"{_pct(amount)}% lost"
        text = f"entry {_money(str(abs(entry)))} {kind} × {share}"
    parts = [head + text]
    if calc.get("problem"):
        parts.append(f"not placed: {calc['problem']}")
    return parts


def describe(calc: dict | None) -> list[str]:
    """The arithmetic as short parts for the run page, joined with " · "
    (stop_calc.describe). [] when there's nothing to show."""
    if calc and calc.get("trigger") is None and calc.get("problem"):
        return [f"not placed: {calc['problem']}"]
    if not calc or calc.get("trigger") is None:
        return []
    basis, own, parts = calc["basis"], calc.get("configured_raw"), []
    if calc.get("moved_by"):
        parts.append(f"moved by {calc['moved_by']}")
    if basis.startswith("group_"):
        return parts + _describe_watched(calc)
    if basis == "usd":
        parts.append(
            f"short {_money(calc['short_fill'])} + ${_money(calc['configured'])} = {_money(own)}"
        )
    elif basis == "level_usd":
        level = Decimal(calc["configured"])
        sign = "+" if level >= 0 else "−"
        parts.append(
            f"credit {_money(calc['entry_credit'])} − P&L {sign}{_money(str(abs(level)))} "
            f"= {_money(own)}"
        )
    elif basis == "level":
        parts.append(
            f"credit {_money(calc['entry_credit'])} × (1 + {_pct(calc['configured'])}%) "
            f"= {_money(own)}"
        )
    elif basis == "entry_premium":
        parts.append(
            f"entry {_money(calc['entry_credit'])} × {_pct(calc['configured'])}% = {_money(own)}"
        )
    elif basis == "closing_order":
        parts.append(
            f"entry {_money(calc['entry_credit'])} − {_money(calc['configured'])} = {_money(own)}"
        )
    elif basis == "entry_lost":
        parts.append(
            f"group entry {_money(calc['group_entry'])} × (1 + {_pct(calc['configured'])}%) "
            f"= {_money(own)}"
        )
    else:
        parts.append(
            f"short {_money(calc['short_fill'])} × (1 + {_pct(calc['configured'])}%) "
            f"= {_money(own)}"
        )
    if calc.get("co_raw") is not None:
        parts.append(
            f"closing order {_money(calc['entry_credit'])} − {_money(calc['co_amount'])} "
            f"= {_money(calc['co_raw'])}"
        )
        if calc.get("co_mode") == "both":
            parts.append("closing order on every side")
        else:
            won = calc.get("winner") == "co"
            parts.append("lower wins: " + ("closing order" if won else "the side's own"))
    trigger = _money(calc["trigger"])
    if Decimal(calc["raw"]) != Decimal(calc["trigger"]):
        parts.append(f"→ {trigger} (rounded down to the {_money(calc['tick'])} tick)")
    else:
        parts.append(f"→ {trigger}")
    if calc.get("problem"):
        parts.append(f"not placed: {calc['problem']}")
    return parts
