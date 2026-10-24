"""Saving and loading bots, and reading their runs (spec: the first
automation, sections 1 and 6). The definition lives in the bot_* tables
(0030); legs, sides and links refer to each other by position."""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from optionsjourney.bots import caps
from optionsjourney.bots.definition import (
    DEFAULT_ENTRY_CHASE,
    DEFAULT_EXIT_CHASE,
    MAX_CONTRACTS,
    MAX_GROUPS,
    MAX_LEGS,
    MAX_TAGS,
    BotDef,
    ChaseDef,
    DefaultAlertDef,
    EntryTimeDef,
    GroupDef,
    LegDef,
    MoveLimits,
    ProfitActionDef,
    TimeActionDef,
    app_watched,
    is_iron_condor,
    is_iron_condor_shape,
    lone_shorts_rest_stops,
    normalize_delta,
    normalize_tags,
    retag,
    tags_with_main,
)  # fmt: skip
from optionsjourney.bots.exit_math import after_expiry, row_legs
from optionsjourney.bots.schedule import normalize_time, parse_date_list, skip_target_day
from optionsjourney.bots.underlyings import UNDERLYINGS, futures
from optionsjourney.bots.validation import validate
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import (
    Bot,
    BotChase,
    BotEntryTime,
    BotExit,
    BotLeg,
    BotLegGroup,
    BotRun,
    BotTradeOverride,
    ManualBot,
    Setting,
    Tag,
)  # fmt: skip
from optionsjourney.services import bot_paper, journal
from optionsjourney.trading.orders import Side

logger = logging.getLogger(__name__)


def as_def(session, bot: Bot) -> BotDef:
    """A bot row as its definition, in a session already open (services/orders.py)."""
    return _to_def(session, bot)


def _to_def(session, bot: Bot) -> BotDef:
    legs = session.scalars(select(BotLeg).where(BotLeg.bot_id == bot.id).order_by(BotLeg.position))
    groups = session.scalars(
        select(BotLegGroup).where(BotLegGroup.bot_id == bot.id).order_by(BotLegGroup.position)
    )
    times = session.scalars(
        select(BotEntryTime).where(BotEntryTime.bot_id == bot.id).order_by(BotEntryTime.time)
    )
    chase = {c.phase: c for c in session.scalars(select(BotChase).where(BotChase.bot_id == bot.id))}

    def chase_def(phase, default):
        row = chase.get(phase)
        if row is None:
            return default
        return ChaseDef(row.start_offset, row.step, row.interval_seconds, row.max_tries,
                        row.exhaust_mode, row.natural_minutes, row.window_minutes,
                        bool(row.switch_to_market), bool(row.ignore_bidless_longs))  # fmt: skip

    return BotDef(
        id=bot.id, name=bot.name, tag_id=bot.tag_id, broker=bot.broker,
        account_number=bot.account_number, underlying=bot.underlying, contracts=bot.contracts,
        live=bot.live, enabled=bot.enabled, max_per_trade=bot.max_per_trade or None,
        max_per_day=bot.max_per_day or None, split_iron_condors=bot.split_iron_condors,
        legs=tuple(
            LegDef(leg.position, Side(leg.side), leg.right, leg.ratio, leg.dte, leg.pick,
                   leg.target, leg.linked_position, leg.exact, leg.group_position,
                   leg.exit_delta_below, leg.exit_delta_above, leg.max_offset)
            for leg in legs
        ),
        groups=tuple(
            GroupDef(g.position, g.name, g.stop_pct, g.target_pct, g.stop_basis, g.stop_unit,
                     g.stop_usd, g.target_unit, g.target_usd, g.on_stop_remainder,
                     g.on_stop_remainder_limit, bool(g.use_profit_actions),
                     tuple(profit_of(raw) for raw in g.profit_actions or ()),
                     g.on_stop_close_group, g.broker_exit or "stop")
            for g in groups
        ),
        stop_co_mode=bot.stop_co_mode, stop_co_amount=bot.stop_co_amount,
        entry_times=tuple(EntryTimeDef(t.time, t.enabled, t.quantity, t.label) for t in times),
        max_entries_per_day=bot.max_entries_per_day, max_open_trades=bot.max_open_trades,
        use_premium_bounds=bot.use_premium_bounds,
        premium_min=bot.premium_min, premium_max=bot.premium_max, frequency=bot.frequency,
        entry_days=tuple(bot.entry_days or ()), monthly_week=bot.monthly_week,
        monthly_weekday=bot.monthly_weekday, specific_dates=tuple(bot.specific_dates or ()),
        use_blackout_days=bot.use_blackout_days, blackout_days=tuple(bot.blackout_days or ()),
        entry_chase=chase_def("entry", DEFAULT_ENTRY_CHASE),
        # No saved exit chase: give up after the last try (what bots before slice 3
        # did), never leg out on a setting the bot was never saved with.
        exit_chase=chase_def("exit", replace(DEFAULT_EXIT_CHASE, exhaust_mode="give_up")),
        use_vix_level=bot.use_vix_level, vix_min=bot.vix_min, vix_max=bot.vix_max,
        use_vix_overnight=bot.use_vix_overnight,
        vix_overnight=MoveLimits(bot.vix_on_min_up_pct, bot.vix_on_max_up_pct,
                                 bot.vix_on_min_down_pct, bot.vix_on_max_down_pct),
        use_vix9d_ratio=bot.use_vix9d_ratio, vix9d_ratio_min=bot.vix9d_ratio_min,
        vix9d_ratio_max=bot.vix9d_ratio_max, use_technicals=bot.use_technicals,
        sma_mode=bot.sma_mode, sma_days_a=bot.sma_days_a, sma_days_b=bot.sma_days_b,
        ema_mode=bot.ema_mode, ema_minutes_a=bot.ema_minutes_a, ema_minutes_b=bot.ema_minutes_b,
        use_intraday_move=bot.use_intraday_move,
        intraday=MoveLimits(bot.intraday_min_up_pct, bot.intraday_max_up_pct,
                            bot.intraday_min_down_pct, bot.intraday_max_down_pct),
        specific_date_sets=tuple(bot.specific_date_sets or ()),
        blackout_date_sets=tuple(bot.blackout_date_sets or ()),
        blackout_dte_sets=tuple(bot.blackout_dte_sets or ()),
        close_time=bot.close_time, close_dte=bot.close_dte or 0,
        use_time_actions=bot.use_time_actions,
        time_actions=tuple(_action_of(raw) for raw in bot.time_actions or ()),
        tags=tuple(bot.tags or ()), archived=bot.archived_at is not None,
        **{name: getattr(bot, name) for name in WATCH_FIELDS},
        alert_name=bot.alert_name, alert_channel=bot.alert_channel or "all",
        use_default_alerts=bool(bot.use_default_alerts),
        default_alerts=tuple(_alert_rule_of(raw) for raw in bot.default_alerts or ()),
        default_alerts_in_app_only=bool(bot.default_alerts_in_app_only),
        use_floating_entry=bool(bot.use_floating_entry), floating_start=bot.floating_start,
        floating_end=bot.floating_end, use_exact_dte=bool(bot.use_exact_dte),
        sizing=bot.sizing or "contracts", max_per_entry_usd=bot.max_per_entry_usd,
        min_short_width=bot.min_short_width,
        move_conflicted_strikes=bool(bot.move_conflicted_strikes),
        per_trade=bool(bot.per_trade),
    )  # fmt: skip


# Slice 7: the watch rules' columns, named as on the BotDef.
WATCH_FIELDS = (
    "use_delta_exit", "position_delta_below", "position_delta_above", "use_sl_ratio_exit",
    "sl_ratio_below", "sl_ratio_above", "use_underlying_exit", "underlying_up_pct",
    "underlying_down_pct", "short_put_tested_pts", "short_call_tested_pts",
)  # fmt: skip


# Slice 9: the alert settings, named as on the BotDef.
ALERT_FIELDS = (
    "alert_name", "alert_channel", "use_default_alerts", "default_alerts",
    "default_alerts_in_app_only",
)  # fmt: skip


def _alert_rule_of(raw: dict) -> DefaultAlertDef:
    """A stored default alert rule (the numbers kept as text, like money)."""
    return DefaultAlertDef(
        str(raw.get("type", "")), raw.get("direction"), _decimal(raw.get("value")),
        _decimal(raw.get("points")),
    )  # fmt: skip


def _alert_rule_row(rule: DefaultAlertDef) -> dict:
    def text(value):
        return None if value is None else f"{value:f}"

    return {"type": rule.type, "direction": rule.direction, "value": text(rule.value),
            "points": text(rule.points)}  # fmt: skip


def profit_of(raw: dict) -> ProfitActionDef:
    """A stored profit action (the numbers kept as text, like money)."""
    return ProfitActionDef(
        _decimal(raw.get("at")) or Decimal(0), int(raw.get("close_pct") or 0),
        _decimal(raw.get("stop_pct")), _decimal(raw.get("stop_usd")),
    )  # fmt: skip


def profit_row(action: ProfitActionDef) -> dict:
    row = {"at": f"{action.at:f}", "close_pct": action.close_pct,
           "stop_pct": None if action.stop_pct is None else f"{action.stop_pct:f}"}  # fmt: skip
    if action.stop_usd is not None:  # slice 19; a row without it reads as before
        row["stop_usd"] = f"{action.stop_usd:f}"
    return row


def _action_of(raw: dict) -> TimeActionDef:
    """A stored time action (the %s are kept as text, like money)."""
    return TimeActionDef(
        str(raw.get("type", "")), int(raw.get("days", 0)), str(raw.get("at", "")),
        _decimal(raw.get("stop_pct")), _decimal(raw.get("target_pct")),
        int(raw["close_pct"]) if raw.get("close_pct") not in (None, "") else None,
    )  # fmt: skip


def _action_row(action: TimeActionDef) -> dict:
    def text(value):
        return None if value is None else f"{value:f}"

    return {"type": action.type, "days": action.days, "at": action.at,
            "stop_pct": text(action.stop_pct), "target_pct": text(action.target_pct),
            "close_pct": action.close_pct}  # fmt: skip


def _write_filters(row: Bot, bot: BotDef) -> None:
    """The market filters and date lists onto the table row (slice 5)."""
    row.use_vix_level, row.vix_min, row.vix_max = bot.use_vix_level, bot.vix_min, bot.vix_max
    row.use_vix_overnight = bot.use_vix_overnight
    moves = bot.vix_overnight
    row.vix_on_min_up_pct, row.vix_on_max_up_pct = moves.min_up, moves.max_up
    row.vix_on_min_down_pct, row.vix_on_max_down_pct = moves.min_down, moves.max_down
    row.use_vix9d_ratio = bot.use_vix9d_ratio
    row.vix9d_ratio_min, row.vix9d_ratio_max = bot.vix9d_ratio_min, bot.vix9d_ratio_max
    row.use_technicals = bot.use_technicals
    row.sma_mode, row.sma_days_a, row.sma_days_b = bot.sma_mode, bot.sma_days_a, bot.sma_days_b
    row.ema_mode, row.ema_minutes_a = bot.ema_mode, bot.ema_minutes_a
    row.ema_minutes_b = bot.ema_minutes_b
    row.use_intraday_move = bot.use_intraday_move
    moves = bot.intraday
    row.intraday_min_up_pct, row.intraday_max_up_pct = moves.min_up, moves.max_up
    row.intraday_min_down_pct, row.intraday_max_down_pct = moves.min_down, moves.max_down
    row.specific_date_sets = list(bot.specific_date_sets)
    row.blackout_date_sets = list(bot.blackout_date_sets)
    row.blackout_dte_sets = list(bot.blackout_dte_sets)


def load(session_factory: sessionmaker, bot_id: int) -> BotDef | None:
    with session_factory() as session:
        bot = session.get(Bot, bot_id)
        return None if bot is None else _to_def(session, bot)


def listing(session_factory: sessionmaker, archived: bool = False) -> list[BotDef]:
    """The bots on the list (deleted ones are hidden), or only the archived ones."""
    shown = Bot.archived_at.is_not(None) if archived else Bot.archived_at.is_(None)
    with session_factory() as session:
        bots = session.scalars(
            select(Bot).where(Bot.hidden.is_(False), shown).order_by(Bot.name)
        )  # fmt: skip
        return [_to_def(session, bot) for bot in bots]


NEW_TAG = -1  # a main tag that doesn't exist yet: made when the bot is saved


def _with_main_tag(session, bot: BotDef) -> BotDef:
    """#278 (decision 0110): the bot's tags, its main one first, with tag_id set to it
    (NEW_TAG when it's new). A tag_id given (an import, a manual bot, a clone) is the main
    tag and among its tags; the form gives none, so the bot keeps its main tag while it's
    still listed. A bot with no tag at all has tag_id None, for validation to name."""
    given = session.get(Tag, bot.tag_id) if bot.tag_id and bot.tag_id != NEW_TAG else None
    row = session.get(Bot, bot.id) if bot.id else None
    kept = session.get(Tag, row.tag_id) if row is not None and row.tag_id else None
    main = given or kept
    names = (given.name, *bot.tags) if given is not None else bot.tags
    tags = tags_with_main(names, main.name if main is not None else None)
    if not tags:
        return replace(bot, tags=(), tag_id=None)
    found = journal.find_tag(session, tags[0])
    return replace(bot, tags=tags, tag_id=found.id if found is not None else NEW_TAG)


def all_tags(session_factory: sessionmaker) -> tuple[str, ...]:
    """What the bot form's tag picker offers (#109): the journal's tags, which since #278
    (decision 0110) hold every bot's tags too. Never a state word."""
    with session_factory() as session:
        return normalize_tags(session.scalars(select(Tag.name)))


def armed(session_factory: sessionmaker) -> list[BotDef]:
    """The bots the clock fires: armed, not archived, not deleted."""
    return [bot for bot in listing(session_factory) if bot.enabled and not bot.archived]


def archived_count(session_factory: sessionmaker) -> int:
    with session_factory() as session:
        return len(session.scalars(
            select(Bot.id).where(Bot.hidden.is_(False), Bot.archived_at.is_not(None))
        ).all())  # fmt: skip


def save(
    session_factory: sessionmaker,
    bot: BotDef,
    now: datetime,
    form_problems: Mapping[str, str] | None = None,
    imported_at: datetime | None = None,
    listed: Callable[[str], bool | None] | None = None,
    imported_from_url: str | None = None,
    manual_bot_id: int | None = None,
) -> tuple[int | None, dict]:
    """Validate and save. `listed(ticker)`: whether the broker lists options on a stock
    (None: it couldn't be asked); asked for a new underlying other than the five the form
    suggests (bots slice 16). Without it (the services' own saves, clones) nothing's asked.
    `form_problems` (from read_form) are merged in, and win
    over validation's message for the same field: they name what was typed. A new
    bot is always disarmed and Live (decision 0061; a Live bot sends nothing until armed); `imported_at` marks one made from a file,
    `imported_from_url` one fetched from a link.
    Saving never arms, goes live, archives or unarchives."""
    with session_factory() as session:
        tag_ids = set(session.scalars(select(Tag.id))) | {NEW_TAG}
        bot = _with_main_tag(session, bot)
    problems = validate(bot, tag_ids) | dict(form_problems or {})
    if not problems and bot.id:
        problem = _underlying_problem(session_factory, bot, now)
        if problem:
            problems["underlying"] = problem
    if not problems and listed is not None:
        problem = _listed_problem(session_factory, bot, listed)
        if problem:
            problems["underlying"] = problem
    if problems:
        return None, problems
    with session_factory() as session:
        found = journal.tags_for(session, bot.tags)  # #278: made if new; first is main
        bot = replace(bot, tag_id=found[0].id, tags=tuple(t.name for t in found))
        row = session.get(Bot, bot.id) if bot.id else None
        if row is None:
            # New bots start disarmed (0009), and Live (0061), whether typed, cloned or imported.
            row = Bot(live=True, enabled=False, created_at=now, imported_at=imported_at,
                      imported_from_url=imported_from_url)  # fmt: skip
            # Manual bots (#158): a per-trade bot is hidden from the moment it exists
            row.hidden, row.manual_bot_id = bot.per_trade, manual_bot_id
            session.add(row)
        elif (
            row.broker != bot.broker
            or row.underlying != bot.underlying
            or (app_watched(bot) and not app_watched(_to_def(session, row)))
        ):
            # Another broker (slice 11) or underlying (slice 16), or a change that makes the
            # app watch a stop it didn't (final review I3, decision 0039): disarmed and
            # Paper, so it's armed and made live again through its own steps (the
            # "watched by the app" box is ticked again).
            row.enabled, row.live = False, False
        row.name, row.tag_id, row.broker = bot.name.strip(), bot.tag_id, bot.broker
        row.account_number, row.underlying, row.contracts = (
            bot.account_number,
            bot.underlying,
            bot.contracts,
        )
        # Decision 0058: 0 stores a blank (worked-out) limit; the columns stay not null
        row.max_per_trade, row.max_per_day = bot.max_per_trade or 0, bot.max_per_day or 0
        # Spec question 7: the journal splits only an iron condor (a put pair and a call
        # pair, core/positions splits by type); any other shape is one trade. #165: the
        # legs may be grouped any way that keeps puts and calls apart.
        row.split_iron_condors = bot.split_iron_condors and is_iron_condor_shape(bot)
        row.updated_at = now
        row.max_entries_per_day, row.max_open_trades = bot.max_entries_per_day, bot.max_open_trades
        row.use_premium_bounds = bot.use_premium_bounds
        row.premium_min, row.premium_max = bot.premium_min, bot.premium_max
        row.frequency, row.entry_days = bot.frequency, list(bot.entry_days)
        row.monthly_week, row.monthly_weekday = bot.monthly_week, bot.monthly_weekday
        row.specific_dates, row.blackout_days = list(bot.specific_dates), list(bot.blackout_days)
        row.use_blackout_days = bot.use_blackout_days
        row.stop_co_mode, row.stop_co_amount = bot.stop_co_mode, bot.stop_co_amount
        _write_filters(row, bot)
        row.close_time, row.close_dte = bot.close_time, bot.close_dte
        row.use_time_actions = bot.use_time_actions
        row.time_actions = [_action_row(a) for a in bot.time_actions]
        row.tags = list(bot.tags)
        for name in WATCH_FIELDS:
            setattr(row, name, getattr(bot, name))
        row.alert_name = bot.alert_name
        row.alert_channel = bot.alert_channel
        row.use_default_alerts = bot.use_default_alerts
        row.default_alerts = [_alert_rule_row(rule) for rule in bot.default_alerts]
        row.default_alerts_in_app_only = bot.default_alerts_in_app_only
        row.use_floating_entry = bot.use_floating_entry
        row.floating_start, row.floating_end = bot.floating_start, bot.floating_end
        row.use_exact_dte = bot.use_exact_dte
        row.sizing, row.max_per_entry_usd = bot.sizing, bot.max_per_entry_usd
        row.min_short_width = bot.min_short_width
        row.move_conflicted_strikes = bot.move_conflicted_strikes
        # Final review I1: a per-trade bot stays one; the bot form doesn't carry the flag
        row.per_trade = bot.per_trade or bool(row.per_trade)
        session.flush()
        for table in (BotLeg, BotLegGroup, BotEntryTime, BotChase):
            session.execute(delete(table).where(table.bot_id == row.id))
        for leg in bot.legs:
            session.add(BotLeg(
                bot_id=row.id, position=leg.position, side=str(leg.side), right=leg.right,
                ratio=leg.ratio, dte=leg.dte, pick=leg.pick, target=leg.target,
                linked_position=leg.link, exact=leg.exact, group_position=leg.group,
                exit_delta_below=leg.exit_delta_below, exit_delta_above=leg.exit_delta_above,
                max_offset=leg.max_offset,
            ))  # fmt: skip
        for group in bot.groups:
            session.add(BotLegGroup(
                bot_id=row.id, position=group.position, name=group.name,
                stop_pct=group.stop_pct, target_pct=group.target_pct,
                stop_basis=group.stop_basis, stop_unit=group.stop_unit, stop_usd=group.stop_usd,
                target_unit=group.target_unit, target_usd=group.target_usd,
                on_stop_remainder=group.on_stop_remainder,
                on_stop_remainder_limit=group.remainder_limit,
                use_profit_actions=group.use_profit_actions,
                profit_actions=[profit_row(a) for a in group.profit_actions],
                on_stop_close_group=group.close_on_stop,
                broker_exit=group.broker_exit,
            ))  # fmt: skip
        for entry in bot.entry_times:
            session.add(BotEntryTime(bot_id=row.id, time=entry.time, enabled=entry.enabled,
                                     quantity=entry.quantity, label=entry.label))  # fmt: skip
        for phase, chase in (("entry", bot.entry_chase), ("exit", bot.exit_chase)):
            session.add(BotChase(bot_id=row.id, phase=phase, start_offset=chase.start_offset,
                                 step=chase.step, interval_seconds=chase.interval_seconds,
                                 max_tries=chase.max_tries, exhaust_mode=chase.exhaust_mode,
                                 natural_minutes=chase.natural_minutes,
                                 window_minutes=chase.window_minutes,
                                 switch_to_market=chase.switch_to_market,
                                 ignore_bidless_longs=chase.ignore_bidless_longs))  # fmt: skip
        session.commit()
        return row.id, {}


def _listed_problem(session_factory, bot: BotDef, listed) -> str | None:
    """A stock typed in (Tracker2's free-text f-underlying) must have an option chain at
    the broker: asked once, when it becomes the bot's underlying."""
    if bot.underlying in UNDERLYINGS:
        return None
    if bot.id:
        with session_factory() as session:
            row = session.get(Bot, bot.id)
            if row is not None and row.underlying == bot.underlying:
                return None
    try:
        found = listed(bot.underlying)
    except Exception:  # noqa: BLE001 -- unknown: refused, never guessed
        logger.exception("couldn't ask the broker for %s's options", bot.underlying)
        found = None
    if found is None:
        return (
            f"Couldn't check {bot.underlying}'s options at your broker. Try again once "
            "TastyTrade or Schwab is connected."
        )
    if not found and futures(bot.underlying):
        return f"TastyTrade lists no futures options on {bot.underlying}. Check the root."
    if not found:
        return f"Your broker lists no options on {bot.underlying}. Check the ticker."
    return None


def _underlying_problem(session_factory, bot: BotDef, now: datetime) -> str | None:
    """Slice 16, question 5: a run reads its bot's underlying (its price, its root), so
    the underlying can't change while anything is open (open_trade_reasons)."""
    with session_factory() as session:
        row = session.get(Bot, bot.id)
        if row is None or row.underlying == bot.underlying:
            return None
    reasons = open_trade_reasons(session_factory, bot.id, now)
    if not reasons:
        return None
    return (
        f"The underlying can't change while the bot has {', '.join(reasons)}. Wait until "
        "they're done."
    )


def _set(session_factory, bot_id, now, **values) -> None:
    with session_factory() as session:
        bot = session.get(Bot, bot_id)
        if bot is None:
            raise LookupError(f"No bot {bot_id}.")
        for name, value in values.items():
            setattr(bot, name, value)
        bot.updated_at = now
        session.commit()


def set_armed(session_factory, bot_id: int, on: bool, now: datetime) -> bool:
    """Arm or disarm. An archived bot can't be armed (Tracker2's api_toggle_bot:
    "unarchive it first"), nor a futures bot, which enters only from the Trade page's
    Manage with a bot (bots slice 17, the author's ruling): returns False and changes
    nothing."""
    with session_factory() as session:
        bot = session.get(Bot, bot_id)
        if bot is None:
            raise LookupError(f"No bot {bot_id}.")
        if on and bot.archived_at is not None:
            return False
        if on and futures(bot.underlying):
            return False  # bots slice 17: a futures bot enters only from the Trade page
        bot.enabled, bot.updated_at = on, now
        if on:
            bot.notice = None  # decision 0054: armed again knowingly
        session.commit()
        return True


def notice_of(session_factory, bot_id: int) -> str | None:
    """Why the app switched this bot off (migration 0034), until it's armed again."""
    with session_factory() as session:
        return session.scalar(select(Bot.notice).where(Bot.id == bot_id))


def set_live(session_factory, bot_id: int, on: bool, now: datetime) -> None:
    _set(session_factory, bot_id, now, live=on)


def set_mode(
    session_factory, bot_id: int, mode: Literal["live", "paper"], now: datetime, deps=None
) -> None:
    """Live sends real orders when armed; Paper pretends (decision 0061). Going Live first
    closes the bot's open paper positions, which needs `deps` (the stream's prices); a
    bot with open paper trades can't go Live without them. Going Live also disarms the
    bot, so it's armed again through the go-live summary and its box (final review I1)."""
    if mode == "live":
        from optionsjourney.services import bot_paper_exits

        if deps is not None:
            bot_paper.end_paper(deps, bot_id, now)
        elif bot_paper_exits.open_runs(session_factory, bot_id):
            raise ValueError("Open paper trades need the app's prices to close.")
    if mode == "live":
        _set(session_factory, bot_id, now, live=True, enabled=False)
    else:
        set_live(session_factory, bot_id, False, now)


def hide(session_factory, bot_id: int, now: datetime) -> None:
    """A bot with runs is disarmed and hidden, so the journal keeps its link."""
    _set(session_factory, bot_id, now, enabled=False, hidden=True)


# --- Clone, archive and import (bots slice 8) -----------------------------------------

NAME_WIDTH = 100  # Bot.name


def _names(session_factory) -> set[str]:
    """Every bot's name, archived and deleted ones too."""
    with session_factory() as session:
        return set(session.scalars(select(Bot.name)))


def _fit(base: str, suffix: str) -> str:
    return f"{base.strip()[: NAME_WIDTH - len(suffix)].rstrip()}{suffix}"


def copy_name(name: str, taken: set[str]) -> str:
    """'IC' -> 'IC (copy)', then 'IC (copy 2)' ... (_next_copy_name); a name that
    already ends in "(copy N)" is stripped first."""
    base = re.sub(r"\s*\(copy(?:\s+\d+)?\)$", "", name).strip()
    candidate, n = _fit(base, " (copy)"), 2
    while candidate in taken:
        candidate, n = _fit(base, f" (copy {n})"), n + 1
    return candidate


def clone(session_factory, bot_id: int, now: datetime) -> tuple[int | None, dict]:
    """A copy of the bot's whole definition (api_clone_bot), disarmed, in the same mode
    (Live or Paper) as the original, with no runs. It goes through save, so a bot that
    no longer passes validation isn't copied: (None, problems)."""
    source = load(session_factory, bot_id)
    if source is None:
        raise LookupError(f"No bot {bot_id}.")
    name = copy_name(source.name, _names(session_factory))
    copy = replace(source, id=None, name=name, archived=False)
    copy_id, problems = save(session_factory, copy, now)
    if copy_id is not None and not source.live:
        set_live(session_factory, copy_id, False, now)
    return copy_id, problems


# Exit states that may mean an order is at the broker, or about to be
# (close_jobs.LIVE_EXIT_STATUSES), and run states with an entry under way.
LIVE_EXITS = ("placing", "pending", "working", "watching", "armed", "firing", "chasing")
ENTERING = ("running", "pending", "working")  # "working": a resting entry (bots slice 17)


def _runs_text(count: int, one: str, many: str, run_ids) -> str:
    ids = sorted(set(run_ids))
    runs = f"run {ids[0]}" if len(ids) == 1 else "runs " + ", ".join(map(str, ids))
    return f"{count} {one if count == 1 else many} ({runs})"


def open_trade_reasons(session_factory, bot_id: int, now: datetime) -> list[str]:
    """Why the bot can't be archived yet (archive.open_trade_reasons), each in plain
    words; empty if nothing is open. Three overlapping checks: a run with an open
    side, an exit that may be live, an entry under way. Exits on options already
    past expiry (every leg of the row) can't be live, so they never block."""
    with session_factory() as session:
        runs = list(session.scalars(select(BotRun).where(BotRun.bot_id == bot_id)))
        ids = [run.id for run in runs]
        exits = list(session.scalars(select(BotExit).where(BotExit.run_id.in_(ids)))) if ids else []
        closed = bot_paper.closed_groups(session, runs)
    by_run: dict[int, list] = {}
    for row in exits:
        by_run.setdefault(row.run_id, []).append(caps.ExitRow.of(row))
    held = [
        caps.RunRow(run.parent_run_id or run.id, run.id, run.status, tuple(run.legs),
                    tuple(by_run.get(run.id, ())), bool(run.filled_legs),
                    closed.get(run.id, frozenset()))
        for run in runs
        if run.status in caps.HOLDING
    ]  # fmt: skip
    reasons = []
    open_ids = caps.open_runs(held, now)
    if open_ids:
        reasons.append(_runs_text(len(open_ids), "open trade", "open trades", open_ids))
    live = [
        row for row in exits
        if row.state in LIVE_EXITS
        and not all(after_expiry(leg.symbol, now) for leg in row_legs(row))
    ]  # fmt: skip
    if live:
        reasons.append(_runs_text(len(live), "exit still working", "exits still working",
                                  [row.run_id for row in live]))  # fmt: skip
    entering = [run.id for run in runs if run.status in ENTERING]
    if entering:
        reasons.append(_runs_text(len(entering), "entry in progress", "entries in progress",
                                  entering))  # fmt: skip
    return reasons


def archive(session_factory, bot_id: int, now: datetime) -> list[str]:
    """Archive the bot (api_archive_bot): refused while anything is open, with the
    reasons; otherwise it's disarmed and archived, and [] comes back."""
    reasons = open_trade_reasons(session_factory, bot_id, now)
    if reasons:
        return reasons
    _set(session_factory, bot_id, now, enabled=False, archived_at=now)
    return []


def unarchive(session_factory, bot_id: int, now: datetime) -> None:
    """Back on the list, still disarmed (api_unarchive_bot)."""
    _set(session_factory, bot_id, now, enabled=False, archived_at=None)


def imported_name(session_factory, name: str) -> str:
    """The name an imported bot is suggested (_imported_name): its own, or with
    " (imported)", " (imported 2)" ... when a bot already has it."""
    taken = _names(session_factory)
    name = name.strip()[:NAME_WIDTH]
    if name not in taken:
        return name
    candidate, n = _fit(name, " (imported)"), 2
    while candidate in taken:
        candidate, n = _fit(name, f" (imported {n})"), n + 1
    return candidate


def import_bot(
    session_factory,
    bot: BotDef,
    exported_at: datetime | None,
    now: datetime,
    form_problems: Mapping[str, str] | None = None,
    listed=None,
    source_url: str | None = None,
) -> tuple[int | None, dict]:
    """Create a bot from a file (api_import_bot), with the importer's name, tag,
    account and sizes already on `bot`; `source_url`, the link it was fetched from.
    Always a new bot, disarmed and in preview, validated like any save; nothing is
    written if there's a problem."""
    new = replace(bot, id=None, archived=False)
    return save(
        session_factory,
        new,
        now,
        form_problems,
        imported_at=exported_at or now,
        listed=listed,
        imported_from_url=source_url,
    )


def imported(session_factory, bot_id: int) -> tuple[datetime | None, str | None]:
    """When the file a bot was imported from was exported, and the link it came
    from; (None, None) for a bot that wasn't imported."""
    with session_factory() as session:
        bot = session.get(Bot, bot_id)
        return (None, None) if bot is None else (bot.imported_at, bot.imported_from_url)


def skip_day(session_factory, bot_id: int, now: datetime, day: date | None = None) -> date | None:
    """Skip a day (Tracker2's api_skip_day): add `day` (default the next entry day)
    to the blackout days. Only an armed bot can, and never a day that's over;
    returns the day, or None if it can't.
    Old dates and ticked lists left while the toggle was off are cleared first
    (_add_blackout_day), and dates that have passed are dropped."""
    now_et = now.astimezone(MARKET_TZ)
    with session_factory() as session:
        bot = session.get(Bot, bot_id)
        if bot is None or not bot.enabled:
            return None
        day = day or skip_target_day(now_et)
        if day < now_et.date():
            return None
        kept = list(bot.blackout_days or ()) if bot.use_blackout_days else []
        if not bot.use_blackout_days:
            bot.blackout_date_sets = []  # dormant lists mustn't come into force either
            bot.blackout_dte_sets = []
        today = now_et.date().isoformat()
        days = {d for d in kept if d >= today} | {day.isoformat()}
        bot.blackout_days, bot.use_blackout_days = sorted(days), True
        bot.updated_at = now
        session.commit()
        return day


def undo_skip(session_factory, bot_id: int, day: date, now: datetime) -> None:
    """Take a skipped day back; with no dates and no lists left, blackout days goes off."""
    with session_factory() as session:
        bot = session.get(Bot, bot_id)
        if bot is None:
            return
        left = [d for d in (bot.blackout_days or ()) if d != day.isoformat()]
        bot.blackout_days = left
        if not left and not bot.blackout_date_sets and not bot.blackout_dte_sets:
            bot.use_blackout_days = False
        bot.updated_at = now
        session.commit()


def skip_many(session_factory, bot_ids, now: datetime) -> tuple[list[int], list[int]]:
    """Skip a day for several bots: (skipped, not armed or not found)."""
    skipped, refused = [], []
    for bot_id in bot_ids:
        (skipped if skip_day(session_factory, bot_id, now) else refused).append(bot_id)
    return skipped, refused


def tag_many(session_factory, bot_ids, add, remove) -> tuple[list[int], list[str], list[str]]:
    """Bulk tagging (bots slice 20; Tracker2's api_bulk_tag_bots): add and/or remove tags
    on several bots, touching nothing but their tags (so armed and live bots too).
    (the ids changed, the names of bots left as they were because they'd pass MAX_TAGS,
    and of those left as they were because they'd have no tag; #278). Removing a bot's
    main tag makes its first other tag the main one (tags_with_main). An id that isn't a
    bot is skipped."""
    changed, too_many, emptied = [], [], []
    with session_factory() as session:
        for bot_id in bot_ids:
            row = session.get(Bot, bot_id)
            if row is None or row.hidden:
                continue
            old = tuple(row.tags or ())
            main = session.get(Tag, row.tag_id) if row.tag_id else None
            new = tags_with_main(retag(old, add, remove), main.name if main else None)
            if len(new) > MAX_TAGS:
                too_many.append(row.name)
            elif not new:
                emptied.append(row.name)
            elif new != old:
                found = journal.tags_for(session, new)
                row.tag_id, row.tags = found[0].id, [t.name for t in found]
                changed.append(bot_id)
        session.commit()
    return changed, too_many, emptied


def runs(session_factory, bot_id: int, limit: int = 100) -> list[BotRun]:
    with session_factory() as session:
        return list(session.scalars(
            select(BotRun).where(BotRun.bot_id == bot_id).order_by(BotRun.id.desc()).limit(limit)
        ))  # fmt: skip


def grouped_runs(session_factory, bot_id: int, limit: int = 100) -> list[tuple[BotRun, int | None]]:
    """The runs page's rows, newest root first, each root followed by its
    tranches as (run, 2), (run, 3) and so on; roots and loose runs have None."""
    found = runs(session_factory, bot_id, limit)
    ids = {run.id for run in found}
    children: dict[int, list[BotRun]] = {}
    for run in found:
        if run.parent_run_id in ids:
            children.setdefault(run.parent_run_id, []).append(run)
    rows: list[tuple[BotRun, int | None]] = []
    for run in found:
        if run.parent_run_id in ids:
            continue
        rows.append((run, None))
        rows += [
            (child, n)
            for n, child in enumerate(sorted(children.get(run.id, []), key=lambda c: c.id), 2)
        ]
    return rows


def today(session_factory, day: date) -> dict[int, list[BotRun]]:
    with session_factory() as session:
        found: dict[int, list[BotRun]] = {}
        for row in session.scalars(
            select(BotRun).where(BotRun.trading_date == day).order_by(BotRun.id)
        ):
            found.setdefault(row.bot_id, []).append(row)
        return found


def run(session_factory, run_id: int) -> BotRun | None:
    with session_factory() as session:
        return session.get(BotRun, run_id)


def exits_of(session_factory, run_id: int) -> list[BotExit]:
    with session_factory() as session:
        return list(
            session.scalars(select(BotExit).where(BotExit.run_id == run_id).order_by(BotExit.id))
        )


def _decimal(text) -> Decimal | None:
    """A number from the form; blank, unreadable or non-finite (NaN, Infinity) is None."""
    try:
        value = Decimal(str(text).strip()) if str(text or "").strip() else None
    except InvalidOperation:
        return None
    return value if value is not None and value.is_finite() else None


def _int(text, default=0) -> int:
    try:
        return int(str(text).strip())
    except TypeError, ValueError:
        return default


def _limit(text) -> int | None:
    """A contract limit: blank is None, worked out (decision 0058); anything else that
    isn't a whole number is 0, which validation refuses."""
    if not str(text or "").strip():
        return None
    return _int(text)


def _contracts(form) -> int:
    """Contracts per entry. Under a fixed value the form hides it (decision 0050), so a
    count out of range there is kept as 1 rather than refused unseen."""
    contracts = _int(form.get("contracts"))
    if form.get("sizing") == "dollars" and not 1 <= contracts <= MAX_CONTRACTS:
        return 1
    return contracts


def _side(text) -> Side:
    """A leg's side; anything unreadable counts as sell, and validation catches the rest."""
    try:
        return Side(str(text).strip().lower())
    except ValueError:
        return Side.SELL


def _all(form: Mapping, key: str) -> list:
    """Every value of a repeated field (a plain dict holds just one)."""
    getlist = getattr(form, "getlist", None)
    if getlist is not None:
        return getlist(key)
    value = form.get(key, [])
    return value if isinstance(value, list) else [value]


def _cap(text) -> int | None:
    """A cap from the form: blank is no cap; a number is kept as typed, and validation
    rejects anything below 1."""
    return _int(text, 0) if str(text or "").strip() else None


def _dates(text) -> tuple[str, ...]:
    """Pasted dates: the good ones sorted, then any unreadable tokens as typed, so
    validation can name them."""
    good, bad = parse_date_list(str(text or ""))
    return good + bad


def _alert_problems(form: Mapping, problems: dict[str, str]) -> None:
    for n, i in enumerate(_alert_rows(form), 1):
        for raw in _alert_texts(form, i):
            if raw and _decimal(raw) is None:
                problems.setdefault("default_alerts", f"Default alert {n}: {raw!r} "
                                                      "isn't a number.")  # fmt: skip


def _form_problems(form: Mapping, count: int, groups: int = 2) -> dict[str, str]:
    """Numbers typed but not readable, which the BotDef would otherwise hold as
    blank (the bot's contracts, or no bound): {field: problem}, naming the text."""
    problems: dict[str, str] = {}
    for key in form:
        found = re.fullmatch(r"leg-(\d+)-target", str(key))
        raw = str(form.get(key) or "").strip()
        if found and raw and _decimal(raw) is None:
            problems[f"leg-{found.group(1)}"] = f"The target {raw!r} isn't a number."
        found = re.fullmatch(r"leg-(\d+)-dte", str(key))
        if found and raw and not raw.isdigit():
            problems[f"leg-{found.group(1)}"] = (
                f"Days to expiration {raw!r} isn't a whole number, 0 or more."
            )
    for k in range(count):
        at = str(form.get(f"time-{k}", "")).strip()
        raw = str(form.get(f"time-{k}-qty", "")).strip()
        if at and raw and _int(raw, 0) == 0:
            problems.setdefault("times", (
                f"entry time {at}: quantity {raw!r} isn't a whole number from 1 to "
                f"{MAX_CONTRACTS}; leave it blank for the bot's contracts"
            ))  # fmt: skip
    for field, word in (("premium_min", "minimum"), ("premium_max", "maximum")):
        raw = str(form.get(field) or "").strip()
        if raw and _decimal(raw) is None:
            problems.setdefault("premium", f"The {word} premium {raw!r} isn't a number.")
    for g in range(groups):
        for suffix, what in (("stop", "stop"), ("target", "profit target"),
                             ("remainder-limit", "limit price after a stop")):  # fmt: skip
            raw = str(form.get(f"group-{g}-{suffix}") or "").strip()
            if raw and _decimal(raw) is None:
                problems.setdefault(f"group-{g}", f"The {what} {raw!r} isn't a number.")
    raw = str(form.get("stop_co_amount") or "").strip()
    if raw and _decimal(raw) is None:
        problems.setdefault("closing_order", f"The closing order amount {raw!r} isn't a number.")
    for key, fields in _FILTER_NUMBERS.items():
        for field, word in fields:
            raw = str(form.get(field) or "").strip()
            if raw and _decimal(raw) is None:
                problems.setdefault(key, f"The {word} {raw!r} isn't a number.")
    for field, word in _PERIODS:
        raw = str(form.get(field) or "").strip()
        if raw and not raw.isdigit():
            problems.setdefault("technicals", f"The {word} {raw!r} isn't a whole number.")
    raw = str(form.get("chase-exit-natural") or "").strip()
    if raw and not raw.isdigit():
        problems.setdefault("chase_exhaust", f"The natural price window {raw!r} isn't a number.")
    raw = str(form.get("chase-entry-window") or "").strip()  # slice 19
    if raw and not raw.isdigit():
        problems.setdefault("chase_exhaust", f"The entry's time limit {raw!r} isn't a whole "
                                             "number of minutes.")  # fmt: skip
    for field, key, what in (("max_per_entry_usd", "sizing", "most buying power"),
                             ("min_short_width", "strikes", "min short width")):  # fmt: skip
        raw = str(form.get(field) or "").strip()
        if raw and _decimal(raw) is None:
            problems.setdefault(key, f"The {what} {raw!r} isn't a number.")
    for key in form:
        found = re.fullmatch(r"leg-(\d+)-(delta-below|delta-above|max-offset)", str(key))
        raw = str(form.get(key) or "").strip()
        if found and raw and _decimal(raw) is None:
            what = found.group(2).replace("-", " ")
            problems.setdefault(f"leg-{found.group(1)}", f"The {what} {raw!r} isn't a number.")
    raw = str(form.get("close_dte") or "").strip()
    if raw and not raw.isdigit():
        problems.setdefault("timed_close", f"The days to expiration {raw!r} isn't a whole number.")
    for n, i in enumerate(_action_rows(form), 1):
        raw = str(form.get(f"ta-{i}-days") or "").strip()
        if not raw.isdigit():
            problems.setdefault(
                "time_actions", f"Time action {n}: the days {raw!r} isn't a whole number."
            )
        for suffix, word in (("stop", "stop %"), ("target", "target %")):
            raw = str(form.get(f"ta-{i}-{suffix}") or "").strip()
            if raw and _decimal(raw) is None:
                problems.setdefault("time_actions", f"Time action {n}: the {word} {raw!r} "
                                                    "isn't a number.")  # fmt: skip
        raw = str(form.get(f"ta-{i}-close") or "").strip()
        if raw and not raw.isdigit():
            problems.setdefault("time_actions", f"Time action {n}: the close % {raw!r} "
                                                "isn't a whole number.")  # fmt: skip
    for field, word in _WATCH_NUMBERS:
        raw = str(form.get(field) or "").strip()
        if raw and _decimal(raw) is None:
            problems.setdefault("watch_rules", f"The {word} {raw!r} isn't a number.")
    for g in range(groups):
        problem = profit_problem(form, g)
        if problem:
            problems.setdefault(f"profit-{g}", problem)
    _alert_problems(form, problems)
    return problems


# The watch rules' number boxes (slice 7): (form field, words)
_WATCH_NUMBERS = (
    ("position_delta_below", "delta below"), ("position_delta_above", "delta above"),
    ("sl_ratio_below", "S/L ratio below"), ("sl_ratio_above", "S/L ratio above"),
    ("underlying_up_pct", "underlying up %"), ("underlying_down_pct", "underlying down %"),
    ("short_put_tested_pts", "short put points"), ("short_call_tested_pts", "short call points"),
)  # fmt: skip


def profit_problem(form: Mapping, g: int) -> str | None:
    """The first profit action box of group `g` that can't be read, in words, or None
    (shared with a trade's own profit actions, bots slice 21)."""
    for n, i in enumerate(_profit_rows(form, g), 1):
        for suffix, word in (("at", "level"), ("stop", "stop")):
            raw = str(form.get(f"pa-{g}-{i}-{suffix}") or "").strip()
            if raw and _decimal(raw) is None:
                return f"Profit action {n}: the {word} {raw!r} isn't a number."
        raw = str(form.get(f"pa-{g}-{i}-close") or "").strip()
        if raw and not raw.isdigit():
            return f"Profit action {n}: the close % {raw!r} isn't a whole number."
    return None


def profits(form: Mapping, g: int) -> tuple[bool, tuple[ProfitActionDef, ...]]:
    """(Use profit actions ticked, the rows typed) for group `g`, as the bot form reads them."""
    return form.get(f"group-{g}-use-profit") == "on", _profits(form, g)


def _profit_count(form: Mapping, g: int) -> int:
    return min(_int(form.get(f"group-{g}-profits")), 10)


def _profit_texts(form: Mapping, g: int, i: int) -> list[str]:
    return [str(form.get(f"pa-{g}-{i}-{part}") or "").strip() for part in ("at", "close", "stop")]


def _profit_rows(form: Mapping, g: int) -> list[int]:
    """The profit action rows read, by their place on the form: a blank row is dropped."""
    return [i for i in range(_profit_count(form, g)) if any(_profit_texts(form, g, i))]


def _profits(form: Mapping, g: int) -> tuple[ProfitActionDef, ...]:
    """A side's profit action rows typed in, in order (a blank row is dropped). An
    unreadable level reads 0 and an unreadable close % -1, for validation (read_form
    names the text)."""
    rows = []
    for i in _profit_rows(form, g):
        at, close, _ = _profit_texts(form, g, i)
        stop = _decimal(form.get(f"pa-{g}-{i}-stop"))
        usd = form.get(f"pa-{g}-{i}-stop-unit") == "usd"  # slice 19: "$ P&L" or "% loss"
        rows.append(ProfitActionDef(
            _decimal(at) or Decimal(0), int(close) if close.isdigit() else (-1 if close else 0),
            None if usd else stop, stop if usd else None,
        ))  # fmt: skip
    return tuple(rows)


def _action_count(form: Mapping) -> int:
    return min(_int(form.get("actions")), 20)


def _action_typed(form: Mapping, i: int) -> bool:
    """A time action row with anything typed in it (a blank row is dropped)."""
    return any(
        str(form.get(f"ta-{i}-{part}") or "").strip()
        for part in ("days", "at", "stop", "target", "close")
    )


def _action_rows(form: Mapping) -> list[int]:
    """The time action rows read, by their place on the form: a blank row is dropped."""
    return [i for i in range(_action_count(form)) if _action_typed(form, i)]


def _actions(form: Mapping) -> tuple[TimeActionDef, ...]:
    """The time action rows typed in, in order; unreadable days count as -1 for
    validation (read_form names the text)."""
    rows = []
    for i in _action_rows(form):
        days = str(form.get(f"ta-{i}-days") or "").strip()
        close = str(form.get(f"ta-{i}-close") or "").strip()
        rows.append(TimeActionDef(
            str(form.get(f"ta-{i}-type") or "dte"), int(days) if days.isdigit() else -1,
            str(form.get(f"ta-{i}-at") or "").strip(),
            _decimal(form.get(f"ta-{i}-stop")), _decimal(form.get(f"ta-{i}-target")),
            int(close) if close.isdigit() else (-1 if close else None),
        ))  # fmt: skip
    return tuple(rows)


_MOVES = (("min_up", "min up %"), ("max_up", "max up %"), ("min_down", "min down %"),
          ("max_down", "max down %"))  # fmt: skip
# The filters' number boxes, by the problem key they're shown under: (form field, words)
_FILTER_NUMBERS = {
    "vix_level": (("vix_min", "minimum VIX"), ("vix_max", "maximum VIX")),
    "vix_overnight": tuple((f"vix_on_{m}", f"VIX overnight {w}") for m, w in _MOVES),
    "vix9d_ratio": (("vix9d_ratio_min", "minimum ratio"), ("vix9d_ratio_max", "maximum ratio")),
    "intraday_move": tuple((f"intraday_{m}", f"intraday {w}") for m, w in _MOVES),
}
_PERIODS = (("sma_days_a", "SMA days"), ("sma_days_b", "second SMA days"),
            ("ema_minutes_a", "EMA minutes"), ("ema_minutes_b", "second EMA minutes"))  # fmt: skip


def _moves(form: Mapping, prefix: str) -> MoveLimits:
    return MoveLimits(*(_decimal(form.get(f"{prefix}_{m}")) for m, _ in _MOVES))


def _period(text) -> int | None:
    """A period from the form: blank or unreadable is None (read_form names the text)."""
    raw = str(text or "").strip()
    return int(raw) if raw.isdigit() else None


def _sets(form: Mapping, key: str) -> tuple[str, ...]:
    """Ticked date lists, in a fixed order; validation names any it doesn't know."""
    return tuple(sorted({str(name) for name in _all(form, key) if str(name).strip()}))


def _count(form: Mapping, key: str, most: int, before: int) -> int:
    """How many legs or groups the form holds (its hidden count), 1 to `most`; a form
    from before slice 13 has no count and holds `before` (an iron condor's)."""
    if not str(form.get(key) or "").strip():
        return before
    return max(1, min(_int(form.get(key), before), most))


def _named(legs: tuple[LegDef, ...], groups: tuple[GroupDef, ...]) -> tuple[GroupDef, ...]:
    """A group left without a name: an iron condor's "Call side" and "Put side" as
    before, otherwise "Group N"."""
    condor = is_iron_condor(BotDef(None, "", None, "", "", legs=legs, groups=groups))

    def name(g: GroupDef) -> str:
        if condor and g.position < 2:
            return ("Call side", "Put side")[g.position]
        return f"Group {g.position + 1}"

    return tuple(g if g.name else replace(g, name=name(g)) for g in groups)


def _rests(form: Mapping, g: int) -> str:
    """#126: the resting-stop box, ticked "stop" and unticked "target", where the form
    showed it; otherwise the posted choice (a hidden one, or a form from before)."""
    if f"group-{g}-rest-shown" in form:
        return "stop" if form.get(f"group-{g}-rest-stop") == "on" else "target"
    return str(form.get(f"group-{g}-rests") or "stop")


def _group(form: Mapping, g: int) -> GroupDef:
    """One group from the form: one stop box read by its style, one target box by its
    unit. The box goes to the column the picker names and the other is cleared."""
    # #124: the unit and basis selects (Tracker2's); a form from before posts one style
    unit = form.get(f"group-{g}-stop-unit")
    if unit is not None:
        style = "usd" if unit == "usd" else str(form.get(f"group-{g}-stop-basis") or "leg_premium")
    else:
        style = str(form.get(f"group-{g}-stop-style") or "leg_premium")
    stop = _decimal(form.get(f"group-{g}-stop"))
    target = _decimal(form.get(f"group-{g}-target"))
    usd_stop = style == "usd"
    usd_target = form.get(f"group-{g}-target-unit") == "usd"
    return GroupDef(
        g, str(form.get(f"group-{g}-name", "") or ""),
        stop_pct=None if usd_stop else stop,
        target_pct=None if usd_target else target,
        stop_basis="leg_premium" if usd_stop else style,
        stop_unit="usd" if usd_stop else "pct",
        stop_usd=stop if usd_stop else None,
        target_unit="usd" if usd_target else "pct",
        target_usd=target if usd_target else None,
        on_stop_remainder=str(form.get(f"group-{g}-remainder") or "close_market"),
        remainder_limit=_decimal(form.get(f"group-{g}-remainder-limit")),
        use_profit_actions=form.get(f"group-{g}-use-profit") == "on",
        profit_actions=_profits(form, g),
        close_on_stop=_pair(form.get(f"group-{g}-pair")),
        broker_exit=_rests(form, g),
    )  # fmt: skip


def _pair(text) -> int | None:
    """The group a stop fill also closes (slice 21): its position, or None for "nothing"."""
    raw = str(text or "").strip()
    return int(raw) if raw.isdigit() else None


def parse_form(form: Mapping) -> BotDef:
    """The editor's form to a BotDef (see read_form for the problems it can't hold)."""
    return read_form(form)[0]


def read_form(form: Mapping) -> tuple[BotDef, dict[str, str]]:
    """The editor's form to a BotDef, and the numbers that couldn't be read. Most
    unreadable numbers become None or 0 and validation reports them by field; an
    entry-time quantity or a premium bound would read as blank, which is valid, so
    those come back as problems for save()."""
    broker, _, number = str(form.get("account", "")).partition(":")
    leg_count = _count(form, "leg_count", MAX_LEGS, 4)
    group_count = _count(form, "group_count", MAX_GROUPS, 2)
    link_texts = [str(form.get(f"leg-{i}-link", "")).strip() for i in range(leg_count)]
    legs = tuple(
        LegDef(
            position=i,
            side=_side(form.get(f"leg-{i}-side")),
            right=form.get(f"leg-{i}-right", "call"),
            ratio=_int(form.get(f"leg-{i}-ratio"), 1),
            dte=_int(form.get(f"leg-{i}-dte") or 0, -1),  # unreadable: refused by its range
            pick=form.get(f"leg-{i}-pick", "premium"),
            target=_leg_target(form, i),
            link=_int(form.get(f"leg-{i}-link"), -1) if link_texts[i] else None,
            exact=form.get(f"leg-{i}-exact") == "on",
            group=_int(form.get(f"leg-{i}-group")),
            # Slice 19: the leg's delta exit and a linked premium wing's max offset
            exit_delta_below=_decimal(form.get(f"leg-{i}-delta-below")),
            exit_delta_above=_decimal(form.get(f"leg-{i}-delta-above")),
            max_offset=_decimal(form.get(f"leg-{i}-max-offset")),
        )
        for i in range(leg_count)
    )  # fmt: skip
    groups = _named(legs, tuple(_group(form, g) for g in range(group_count)))
    count = min(_int(form.get("times")), 50)
    times = tuple(
        EntryTimeDef(
            _clock_text(form[f"time-{k}"], seconds=True),
            form.get(f"time-{k}-on") == "on",
            _int(form.get(f"time-{k}-qty"), 0) or None,
            str(form.get(f"time-{k}-label", "")).strip() or None,
        )
        for k in _time_rows(form)
    )

    def chase(phase, default):
        start = _decimal(form.get(f"chase-{phase}-start"))  # 0 is a valid start offset
        return ChaseDef(
            default.start_offset if start is None else start,
            _decimal(form.get(f"chase-{phase}-step")) or default.step,
            _int(form.get(f"chase-{phase}-interval"), default.interval_seconds),
            _int(form.get(f"chase-{phase}-tries"), default.max_tries),
            str(form.get(f"chase-{phase}-exhaust") or default.exhaust_mode),
            _int(form.get(f"chase-{phase}-natural"), default.natural_minutes),
            # Slice 19: the entry's time limit (blank: none; unreadable: -1, refused) and
            # the exit's switch to market after the last try
            _window(form.get(f"chase-{phase}-window")),
            form.get(f"chase-{phase}-market") == "on",
            phase == "exit" and form.get("chase-exit-bidless") == "on",  # decision 0065
        )  # fmt: skip

    bot = BotDef(
        id=_int(form.get("id")) or None, name=str(form.get("name", "")),
        tag_id=_int(form.get("tag_id")) or None, broker=broker or "tastytrade",
        account_number=number,
        underlying=str(form.get("underlying") or "").strip().upper() or "SPX",
        contracts=_contracts(form), max_per_trade=_limit(form.get("max_per_trade")),
        max_per_day=_limit(form.get("max_per_day")), split_iron_condors=form.get("split") == "on",
        legs=legs, groups=groups, entry_times=times,
        entry_chase=chase("entry", DEFAULT_ENTRY_CHASE),
        exit_chase=chase("exit", DEFAULT_EXIT_CHASE),
        max_entries_per_day=_cap(form.get("max_entries_per_day")),
        max_open_trades=_cap(form.get("max_open_trades")),
        use_premium_bounds=form.get("use_premium_bounds") == "on",
        premium_min=_decimal(form.get("premium_min")),
        premium_max=_decimal(form.get("premium_max")),
        frequency=form.get("frequency", "daily") or "daily",
        entry_days=tuple(sorted({_int(d) for d in _all(form, "entry_days")})),
        monthly_week=_int(form.get("monthly_week"), 0) or None,
        monthly_weekday=_int(form.get("monthly_weekday"), 0) or None,
        specific_dates=_dates(form.get("specific_dates")),
        use_blackout_days=form.get("use_blackout_days") == "on",
        blackout_days=_dates(form.get("blackout_days")),
        stop_co_mode=form.get("stop_co_mode", "off") or "off",
        stop_co_amount=_decimal(form.get("stop_co_amount")),
        use_vix_level=form.get("use_vix_level") == "on",
        vix_min=_decimal(form.get("vix_min")), vix_max=_decimal(form.get("vix_max")),
        use_vix_overnight=form.get("use_vix_overnight") == "on",
        vix_overnight=_moves(form, "vix_on"),
        use_vix9d_ratio=form.get("use_vix9d_ratio") == "on",
        vix9d_ratio_min=_decimal(form.get("vix9d_ratio_min")),
        vix9d_ratio_max=_decimal(form.get("vix9d_ratio_max")),
        use_technicals=form.get("use_technicals") == "on",
        sma_mode=str(form.get("sma_mode") or "").strip() or None,
        sma_days_a=_period(form.get("sma_days_a")), sma_days_b=_period(form.get("sma_days_b")),
        ema_mode=str(form.get("ema_mode") or "").strip() or None,
        ema_minutes_a=_period(form.get("ema_minutes_a")),
        ema_minutes_b=_period(form.get("ema_minutes_b")),
        use_intraday_move=form.get("use_intraday_move") == "on",
        intraday=_moves(form, "intraday"),
        specific_date_sets=_sets(form, "specific_date_sets"),
        blackout_date_sets=_sets(form, "blackout_date_sets"),
        blackout_dte_sets=_sets(form, "blackout_dte_sets"),
        close_time=str(form.get("close_time") or "").strip() or None,
        close_dte=_int(form.get("close_dte"), 0),
        use_time_actions=form.get("use_time_actions") == "on",
        time_actions=_actions(form),
        tags=normalize_tags(str(form.get("tags") or "").split(",")),
        use_delta_exit=form.get("use_delta_exit") == "on",
        use_sl_ratio_exit=form.get("use_sl_ratio_exit") == "on",
        use_underlying_exit=form.get("use_underlying_exit") == "on",
        **{field: _decimal(form.get(field)) for field, _ in _WATCH_NUMBERS},
        alert_name=str(form.get("alert_name") or "").strip() or None,
        alert_channel=str(form.get("alert_channel") or "all"),
        use_default_alerts=form.get("use_default_alerts") == "on",
        default_alerts=_alert_rules(form),
        default_alerts_in_app_only=form.get("default_alerts_in_app_only") == "on",
        use_floating_entry=form.get("use_floating_entry") == "on",
        floating_start=_clock_text(form.get("floating_start")) or None,
        floating_end=_clock_text(form.get("floating_end")) or None,
        # A form without the box (one from before slice 14) keeps exact DTE on
        use_exact_dte=form.get("use_exact_dte") == "on" if "exact_dte_shown" in form else True,
        sizing=str(form.get("sizing") or "contracts"),
        max_per_entry_usd=_decimal(form.get("max_per_entry_usd")),
        min_short_width=_decimal(form.get("min_short_width")),
        move_conflicted_strikes=form.get("move_conflicted_strikes") == "on",
    )  # fmt: skip
    return lone_shorts_rest_stops(bot), _form_problems(form, count, group_count)


def _clock_text(text, seconds: bool = False) -> str:
    """A typed time as stored (945 is 09:45); text that isn't a time stays as typed, so
    validation names it."""
    raw = str(text or "").strip()
    return normalize_time(raw, seconds) or raw


def _window(text) -> int | None:
    """The entry chase's time limit from the form: blank is none; anything else not a
    whole number reads -1, which validation refuses (read_form names the text)."""
    raw = str(text or "").strip()
    if not raw:
        return None
    return int(raw) if raw.isdigit() else -1


# Number boxes whose typed text read_form may not be able to hold: a decimal or a
# whole number. typed_texts keeps such text so a redrawn form shows it as typed.
_DECIMAL_BOXES = re.compile(
    r"group-\d+-(stop|target|remainder-limit)|leg-\d+-target|premium_(min|max)|"
    r"leg-\d+-(delta-below|delta-above|max-offset)|max_per_entry_usd|min_short_width|"
    r"stop_co_amount|ta-\d+-(stop|target)|pa-\d+-\d+-(at|stop)|da-\d+-(value|points)|"
    + "|".join(
        [field for fields in _FILTER_NUMBERS.values() for field, _ in fields]
        + [field for field, _ in _WATCH_NUMBERS]
    )
)
_WHOLE_BOXES = re.compile(
    r"time-\d+-qty|ta-\d+-(days|close)|pa-\d+-\d+-close|close_dte|chase-exit-natural|leg-\d+-dte|"
    r"chase-entry-window|" + "|".join(field for field, _ in _PERIODS)
)


def _time_rows(form: Mapping) -> list[int]:
    """The entry time rows read, by their place on the form: a blank time is dropped."""
    count = min(_int(form.get("times")), 50)
    return [k for k in range(count) if str(form.get(f"time-{k}", "")).strip()]


def _row_keys(form: Mapping) -> dict[str, str]:
    """{field prefix on the form: the prefix of the row it's read into} for the rows
    read without their blank rows (entry times, time and profit actions, default
    alerts), so text shown again lands on its own row, with that row's own choices."""
    out = {}
    for prefix, rows in (("time", _time_rows(form)), ("ta", _action_rows(form)),
                         ("da", _alert_rows(form))):  # fmt: skip
        out |= {f"{prefix}-{i}-": f"{prefix}-{n}-" for n, i in enumerate(rows)}
    for g in range(MAX_GROUPS):
        out |= {f"pa-{g}-{i}-": f"pa-{g}-{n}-" for n, i in enumerate(_profit_rows(form, g))}
    return out


_ROW_FIELD = re.compile(r"((?:time|ta|da)-\d+-|pa-\d+-\d+-)(.*)")


def typed_texts(form: Mapping) -> dict[str, str]:
    """{field: text} for each number box whose text couldn't be read, as typed, keyed
    by where the redrawn form shows it (a row moves up past dropped blank rows)."""
    rows = _row_keys(form)
    out = {}
    for key in form:
        raw = str(form.get(key) or "").strip()
        if not raw:
            continue
        decimal = _DECIMAL_BOXES.fullmatch(str(key)) and _decimal(raw) is None
        if decimal or (_WHOLE_BOXES.fullmatch(str(key)) and not raw.isdigit()):
            row = _ROW_FIELD.fullmatch(str(key))
            if row is None:
                out[str(key)] = raw
            elif row.group(1) in rows:  # a row that isn't read isn't shown again
                out[rows[row.group(1)] + row.group(2)] = raw
    return out


# The form keys that carry a leg's or a group's position, as {kind: patterns}.
_NUMBERED = {
    "leg": (re.compile(r"(leg-)(\d+)(-.*)?"),),
    "group": (re.compile(r"(group-)(\d+)(-.*)?"), re.compile(r"(profit-)(\d+)()"),
              re.compile(r"(pa-)(\d+)(-.*)")),
}  # fmt: skip


def renumbered(values: Mapping[str, str], kind: str, removed: int) -> dict[str, str]:
    """`values` keyed by form field after the leg or group (`kind`) at `removed` is
    gone (definition.remove_leg / remove_group): its own keys dropped, later ones
    moved down one."""
    out = {}
    for key, value in values.items():
        for pattern in _NUMBERED[kind]:
            found = pattern.fullmatch(key)
            if found:
                n = int(found.group(2))
                if n == removed:
                    break
                if n > removed:
                    key = f"{found.group(1)}{n - 1}{found.group(3) or ''}"
                out[key] = value
                break
        else:
            out[key] = value
    return out


def made_room(values: Mapping[str, str], kind: str, at: int) -> dict[str, str]:
    """`values` keyed by form field after a leg (`kind`) is put in at `at`
    (definition.add_linked_leg): the keys from `at` on moved up one."""
    out = {}
    for key, value in values.items():
        for pattern in _NUMBERED[kind]:
            found = pattern.fullmatch(key)
            if found and int(found.group(2)) >= at:
                key = f"{found.group(1)}{int(found.group(2)) + 1}{found.group(3) or ''}"
                break
        out[key] = value
    return out


def _leg_target(form: Mapping, i: int) -> Decimal | None:
    """A leg's target as typed; a delta reads "16" and "0.16" alike (normalize_delta)."""
    target = _decimal(form.get(f"leg-{i}-target"))
    if form.get(f"leg-{i}-pick") == "delta":
        return normalize_delta(target)
    return target


def _alert_count(form: Mapping) -> int:
    return min(_int(form.get("alerts")), 20)


def _alert_texts(form: Mapping, i: int) -> tuple[str, str]:
    return tuple(str(form.get(f"da-{i}-{part}") or "").strip() for part in ("value", "points"))


def _alert_rows(form: Mapping) -> list[int]:
    """The default alert rows read, by their place on the form: a row without its
    kind's number is dropped."""
    rows = []
    for i in range(_alert_count(form)):
        value, points = _alert_texts(form, i)
        kind = str(form.get(f"da-{i}-type") or "pnl_pct")
        if value if kind == "pnl_pct" else points:
            rows.append(i)
    return rows


def _alert_rules(form: Mapping) -> tuple[DefaultAlertDef, ...]:
    """The default alert rows typed in, in order; a row with no number is dropped. An
    unreadable number reads None, for validation (read_form names the text)."""
    rows = []
    for i in _alert_rows(form):
        value, points = _alert_texts(form, i)
        kind = str(form.get(f"da-{i}-type") or "pnl_pct")
        if kind == "pnl_pct":
            rows.append(DefaultAlertDef(kind, str(form.get(f"da-{i}-direction") or "above"),
                                        _decimal(value)))  # fmt: skip
        else:
            rows.append(DefaultAlertDef(kind, points=_decimal(points)))
    return tuple(rows)


LEAVE_PAUSE_KEY = "_leave_paused"  # "on" (until resumed), an ISO time (until then), or "off"


def leave_pause(session_factory, now: datetime) -> tuple[bool, datetime | None]:
    """Whether "leave" is paused, and until when (None: until resumed). No row,
    or "off", isn't paused; a pause whose time has passed has lapsed. If it
    can't be read it counts as paused: the wing is sold, never left unwatched."""
    try:
        with session_factory() as session:
            row = session.get(Setting, LEAVE_PAUSE_KEY)
            value = None if row is None else row.value
    except Exception:  # noqa: BLE001 -- fail toward selling the wing
        logger.exception("couldn't read the leave pause; treating it as paused")
        return True, None
    if value is None or value == "off":
        return False, None
    if value == "on":
        return True, None
    try:
        until = datetime.fromisoformat(value)
    except ValueError:
        return True, None
    if until.tzinfo is None:
        return True, None
    return now < until, until


def leave_paused(session_factory, now: datetime) -> bool:
    return leave_pause(session_factory, now)[0]


def leave_in_use(session_factory) -> bool:
    """Whether any group or any one trade is set to leave its wing after a stop
    (automation_bp._monitoring_is_relevant): only then does the pause change anything."""
    with session_factory() as session:
        group = select(BotLegGroup.id).where(BotLegGroup.on_stop_remainder == "leave")
        trade = select(BotTradeOverride.id).where(BotTradeOverride.on_stop_remainder == "leave")
        return session.scalar(group.limit(1)) is not None or (
            session.scalar(trade.limit(1)) is not None
        )


def _put_setting(session_factory, key: str, value: str, now: datetime) -> None:
    with session_factory() as session:
        row = session.get(Setting, key) or Setting(key=key)
        row.value, row.updated_at = value, now
        session.add(row)
        session.commit()


def pause_leave(session_factory, until: datetime | None, now: datetime) -> None:
    """Pause "leave" until `until`, or until resumed (None)."""
    value = "on" if until is None else until.astimezone(UTC).isoformat()
    _put_setting(session_factory, LEAVE_PAUSE_KEY, value, now)


def resume_leave(session_factory, now: datetime) -> None:
    _put_setting(session_factory, LEAVE_PAUSE_KEY, "off", now)


def label(session_factory, bot_id: int) -> str | None:
    """The name a bot's runs show under: a manual bot's per-trade bot (#158) shows its
    manual bot's name as it is now (a deleted one's last name); any other bot its own."""
    with session_factory() as session:
        row = session.get(Bot, bot_id)
        if row is None:
            return None
        if row.per_trade and row.manual_bot_id is not None:
            manual = session.get(ManualBot, row.manual_bot_id)
            if manual is not None:
                return manual.name
        return row.name
