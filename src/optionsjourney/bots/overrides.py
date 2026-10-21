"""What one trade's group reads (bots slice 21; Tracker2's exits.effective_stop_loss_pct,
effective_on_stop_remainder, and the drawer's
per-group profit actions): the bot's group with this trade's own copy laid over it. Pure.

A trade's copy lives in bot_trade_overrides, one row per run and group. A field that's
None there is the group's own, so a trade nobody changed reads exactly its bot. Every
path that reads these settings of a held trade goes through here: the stop fill and a
paired close (the wing rule), the watch pass (profit actions), a stop move's style, the
chips."""

from dataclasses import dataclass, replace
from decimal import Decimal

from optionsjourney.bots.definition import BotDef, GroupDef, ProfitActionDef


@dataclass(frozen=True)
class TradeGroup:
    """One group's own settings on one trade (BotPositionOverride). None: the group's."""

    stop_pct: Decimal | None = None  # the % you set (slice 6), in the group's own % style
    target_pct: Decimal | None = None
    stop_unit: str | None = None  # "pct" or "usd" (slice 21)
    stop_usd: Decimal | None = None
    co_mode: str | None = None  # the closing-order overlay: off, both, tighter
    co_amount: Decimal | None = None
    on_stop_remainder: str | None = None
    remainder_limit: Decimal | None = None
    use_profit_actions: bool | None = None
    profit_actions: tuple[ProfitActionDef, ...] | None = None  # replaces the group's list


def effective_group(group: GroupDef, mine: TradeGroup | None) -> GroupDef:
    """The group as this trade runs it. A $ stop of the trade's switches the unit; a % the
    trade set keeps the group's own % style (a $ group's % reads as % above the short's
    fill, as every % move does). The wing rule's limit goes with its mode: a mode set
    without a limit means the bid."""
    if mine is None:
        return group
    changes: dict = {}
    if mine.stop_unit == "usd" and mine.stop_usd is not None:
        changes |= {"stop_unit": "usd", "stop_usd": mine.stop_usd}
    elif mine.stop_pct is not None:
        basis = group.stop_basis if group.stop_unit == "pct" else "leg_premium"
        changes |= {"stop_unit": "pct", "stop_pct": mine.stop_pct, "stop_basis": basis}
    if mine.target_pct is not None and group.target_unit == "pct":
        # A trade's target % never switches a $ group's unit: its profit actions and time
        # actions are in the group's own unit (a $1.50 action read as 1.50% would fire at
        # once). A slice 6 % on a $ group is its chip only.
        changes["target_pct"] = mine.target_pct
    if mine.on_stop_remainder is not None:
        changes |= {"on_stop_remainder": mine.on_stop_remainder,
                    "remainder_limit": mine.remainder_limit}  # fmt: skip
    if mine.use_profit_actions is not None:
        changes["use_profit_actions"] = mine.use_profit_actions
    if mine.profit_actions is not None:
        changes["profit_actions"] = tuple(mine.profit_actions)
    return replace(group, **changes) if changes else group


@dataclass(frozen=True)
class TradeTimed:
    """One trade's own timed close (BotRunTimedCloseOverride): the time is the switch
    (None: off for this trade), a None DTE is 0."""

    close_time: str | None
    close_dte: int | None


def effective_timed(bot: BotDef, mine: TradeTimed | None) -> tuple[str | None, int, str]:
    """(time, DTE, "trade" or "bot") in force for one trade (timed_close.effective_settings):
    the trade's own when it has one (wholesale: its blank time means off), else the bot's."""
    if mine is not None:
        return mine.close_time or None, mine.close_dte or 0, "trade"
    return bot.close_time or None, bot.close_dte or 0, "bot"
