"""Managing one bot trade from the journal (spec: bots slice 6; Tracker2's
management.py and override_tags.py). Pure: the chips that say what a trade runs
with that differs from its bot, the bot side's levels in words, and the checks
on what you type. services/bot_manage.py does the rest.

A chip appears only when something differs: each level you set is compared to
what the bot's side gives (override_tags._exit_tags), so typing the bot's own
number again makes its chip go away."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from optionsjourney.bots.definition import GroupDef

STOP_RANGE = (Decimal(1), Decimal(1000))  # a side's own % stop (validation._stop_problem)
TARGET_RANGE = (Decimal(1), Decimal(99))  # % of the side's credit kept
# Decision 0048: on a trade that filled for a debit, % of the debit gained (200% = triple)
DEBIT_TARGET_RANGE = (Decimal(1), Decimal(1000))


def target_range(entry: Decimal | None) -> tuple[Decimal, Decimal]:
    """The % a target typed on a trade may be, by its fill (`entry`, negative for a debit):
    1-1000 on a debit, 1-99 on a credit (100% or more of a credit can't be kept)."""
    return DEBIT_TARGET_RANGE if entry is not None and entry < 0 else TARGET_RANGE


@dataclass(frozen=True)
class Chip:
    kind: str  # "stop", "target", "off", "target_off" (slice 18), "closed" or "futures"
    label: str
    title: str

    @property
    def warning(self) -> bool:
        return self.kind == "off"


@dataclass(frozen=True)
class Override:
    """What you changed on one side of one trade (Tracker2's BotPositionOverride)."""

    stop_pct: Decimal | None = None
    target_pct: Decimal | None = None
    exits_off: bool = False  # both switched off (slice 6's single switch)
    stop_off: bool = False  # bots slice 18: Tracker2's stop_loss_off
    target_off: bool = False  # profit_target_off
    # Bots slice 21: the stop style, the wing rule and the profit actions set on this trade
    stop_unit: str | None = None
    stop_usd: Decimal | None = None
    co_mode: str | None = None
    co_amount: Decimal | None = None
    on_stop_remainder: str | None = None
    remainder_limit: Decimal | None = None
    own_profit_actions: bool = False

    @property
    def no_stop(self) -> bool:
        return self.exits_off or self.stop_off

    @property
    def no_target(self) -> bool:
        return self.exits_off or self.target_off


def _num(value: Decimal) -> str:
    """override_tags._num: 150 for 150.00, 0.8 for 0.80, never an exponent."""
    d = Decimal(value).normalize()
    return f"{d.quantize(Decimal(1)) if d == d.to_integral() else d:f}"


def _money(value: Decimal) -> str:
    return f"${Decimal(value).quantize(Decimal('0.01')):f}"


def stop_text(group: GroupDef | None, co_mode: str = "off", co_amount=None,
              watched: bool = False) -> str:  # fmt: skip
    """The bot side's stop in words: "150%", "150% of the entry credit", "50% of the
    entry price" (a group the app watches, bots slice 13), "$2.00 a contract" (with
    "(watched by the app)" on a watched group), "closing order $0.80", or "none"; with
    the closing-order overlay when the bot has one on (never on a watched group)."""
    if group is None or group.stop_value is None:
        return "none"
    if group.stop_unit == "usd":
        text = f"{_money(group.stop_usd)} a contract"
        return f"{text} (watched by the app)" if watched else text
    if group.stop_basis == "group_entry":
        return f"{_num(group.stop_pct)}% of the entry price"
    if group.stop_basis == "closing_order":
        return f"closing order {_money(group.stop_pct)}"
    text = f"{_num(group.stop_pct)}%"
    if group.stop_basis == "entry_premium":
        text += " of the entry credit"
    if co_mode != "off" and co_amount is not None and not watched:
        text += f" with the closing order {_money(co_amount)} ({co_mode})"
    return text


def target_text(group: GroupDef | None) -> str:
    if group is None or group.target_value is None:
        return "none"
    if group.target_unit == "usd":
        return f"{_money(group.target_usd)} a contract"
    return f"{_num(group.target_pct)}%"


def _same_stop(group: GroupDef | None, pct: Decimal, co_mode: str, watched: bool = False) -> bool:
    """Your % gives the stop the bot's side would: the side's own % style, same
    number, and no closing-order overlay (a moved stop has none; a watched group never
    has one)."""
    return (
        group is not None
        and group.stop_unit == "pct"
        and group.stop_basis != "closing_order"
        and group.stop_pct is not None
        and (co_mode == "off" or watched)
        and Decimal(group.stop_pct) == Decimal(pct)
    )


def _same_target(group: GroupDef | None, pct: Decimal) -> bool:
    return (
        group is not None
        and group.target_unit == "pct"
        and group.target_pct is not None
        and Decimal(group.target_pct) == Decimal(pct)
    )


def chips(
    group: GroupDef | None,
    override: Override | None,
    closed: int = 0,
    size: int = 0,
    co_mode: str = "off",
    co_amount=None,
    watched: bool = False,
) -> list[Chip]:
    """The chips for one side: what you changed that differs from the bot's side,
    exits off, and how much of it you closed (`closed` of `size`, in units).
    `watched`: the app watches the group's stop on its P&L (bots slice 13)."""
    out = []
    both = override is not None and override.no_stop and override.no_target
    if both:
        out.append(Chip("off", "Exits off: not protected", (
            "You turned this group's exits off: it has no stop and no target. Its timed close "
            "and rules still close it. Turn them back on from the run page."
        )))  # fmt: skip
    elif override is not None and override.no_stop:
        out.append(Chip("off", "Stop off: no stop", (
            "You switched this group's stop off: nothing closes it on a loss. Its target "
            "still works. Switch the stop back on from the run page."
        )))  # fmt: skip
    elif override is not None and override.no_target:
        out.append(Chip("target_off", "Target off", (
            "You switched this group's profit target off. Its stop still works. Switch the "
            "target back on from the run page."
        )))  # fmt: skip
    if override is not None and not both:
        if (
            not override.no_stop
            and override.stop_pct is not None
            and not _same_stop(group, override.stop_pct, co_mode, watched)
        ):
            bots = stop_text(group, co_mode, co_amount, watched)
            out.append(Chip(
                "stop", f"Stop {_num(override.stop_pct)}%",
                f"Stop changed for this trade (bot: {bots})",
            ))  # fmt: skip
        if (
            not override.no_target
            and override.target_pct is not None
            and not _same_target(group, override.target_pct)
        ):
            out.append(Chip(
                "target", f"Target {_num(override.target_pct)}%",
                f"Target changed for this trade (bot: {target_text(group)})",
            ))  # fmt: skip
    if override is not None and not override.no_stop:
        out += _style_chips(group, override, co_mode, co_amount, watched)
    if override is not None and override.on_stop_remainder is not None:
        mine = (override.on_stop_remainder, override.remainder_limit)
        own = (group.on_stop_remainder, group.remainder_limit) if group else ("close_market", None)
        if mine != own:
            out.append(Chip(
                "wing", f"After the stop: {wing_words(*mine)}",
                f"What happens to the rest after the stop, for this trade (bot: "
                f"{wing_words(*own)})",
            ))  # fmt: skip
    if override is not None and override.own_profit_actions:
        out.append(Chip(
            "profit", "Profit actions: this trade's",
            "This trade runs its own profit actions on this group, not the bot's.",
        ))  # fmt: skip
    if closed > 0 and size > 0:
        out.append(Chip(
            "closed", f"Closed {closed} of {size}",
            f"You closed {closed} of this group's {size}; the rest is still open.",
        ))  # fmt: skip
    return out


WING_WORDS = {
    "close_market": "sell now", "close_limit": "sell at a limit", "close_chase": "chase",
    "leave": "leave open",
}  # fmt: skip


def wing_words(mode: str, limit=None) -> str:
    """A wing rule in words: "sell now", "sell at a limit at 0.15" (or "at the bid")."""
    words = WING_WORDS.get(mode, mode)
    if mode == "close_limit":
        words += f" at {_num(limit)}" if limit is not None else " at the bid"
    return words


def _style_chips(group, override: Override, co_mode, co_amount, watched) -> list[Chip]:
    """Bots slice 21: a $ stop set on this trade, and its own closing-order overlay, when
    they differ from the bot's."""
    out = []
    bots = stop_text(group, co_mode, co_amount, watched)
    if override.stop_unit == "usd" and override.stop_usd is not None:
        own = group.stop_usd if group is not None and group.stop_unit == "usd" else None
        if own != override.stop_usd:
            out.append(Chip("stop", f"Stop {_money(override.stop_usd)} a contract",
                            f"Stop changed for this trade (bot: {bots})"))  # fmt: skip
    if override.co_mode is not None:
        mine = (override.co_mode, override.co_amount if override.co_mode != "off" else None)
        own = (co_mode, co_amount if co_mode != "off" else None)
        if mine != own:
            label = "Closing order off"
            if override.co_mode != "off":
                label = f"Closing order {_money(override.co_amount)} ({override.co_mode})"
            out.append(Chip("stop", label, f"Closing order changed for this trade (bot: {bots})"))
    return out


def read_pct(text: str, bounds: tuple[Decimal, Decimal], what: str):
    """(the % typed, None) or (None, why not): a number within `bounds`."""
    try:
        value = Decimal(str(text).strip().rstrip("%"))
    except InvalidOperation, ValueError:
        return None, f"The {what} must be a number."
    if not value.is_finite() or not bounds[0] <= value <= bounds[1]:
        return None, f"The {what} must be from {_num(bounds[0])}% to {_num(bounds[1])}%."
    return value, None


USD_RANGE = (Decimal("0.01"), Decimal(1000))  # a $ stop per contract, in option price


def read_usd(text: str, what: str):
    """(the dollars typed, None) or (None, why not): an amount above $0 (bots slice 21; a
    $ stop is the loss per contract in option price, validation._stop_problem)."""
    try:
        value = Decimal(str(text).strip().lstrip("$"))
    except InvalidOperation, ValueError:
        return None, f"The {what} must be a dollar amount."
    if not value.is_finite() or not USD_RANGE[0] <= value <= USD_RANGE[1]:
        return None, f"The {what} must be a dollar amount above $0, up to {_money(USD_RANGE[1])}."
    return value, None


def quantity_problem(text: str, held: int):
    """(the quantity, None) or (None, why not): a whole number from 1 to what's held
    (manual_close.parse_quantity and its n > held refusal)."""
    try:
        n = int(str(text).strip())
    except ValueError:
        return None, "The quantity must be a whole number."
    if n < 1:
        return None, "The quantity must be 1 or more."
    if held < 1:
        return None, "Nothing of this group is held."
    if n > held:
        return None, f"Only {held} of this group {'is' if held == 1 else 'are'} held."
    return n, None
