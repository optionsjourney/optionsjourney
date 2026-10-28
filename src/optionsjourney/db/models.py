"""Database tables."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    false,
    true,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column

from optionsjourney.db import Base, Money, UTCDateTime


class Account(Base):
    """A broker account seen during a sync."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("broker", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(String(20))
    number: Mapped[str] = mapped_column(String(40))
    nickname: Mapped[str | None] = mapped_column(String(100))
    account_type: Mapped[str | None] = mapped_column(String(50))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class BrokerTransaction(Base):
    """A broker transaction exactly as the broker sent it: the source of truth.

    Fills are parsed from `raw` whenever they're needed, so a parsing fix never
    needs a re-sync. A row is replaced only when the broker's own record changes
    (TastyTrade finalizes estimated fees the next day, for example)."""

    __tablename__ = "broker_transactions"
    __table_args__ = (UniqueConstraint("broker", "transaction_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(String(20))
    account_number: Mapped[str] = mapped_column(String(40), index=True)
    transaction_id: Mapped[str] = mapped_column(String(60))
    transaction_type: Mapped[str] = mapped_column(String(40))
    executed_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    raw: Mapped[dict] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SyncState(Base):
    """How far each account has been synced, and how the last attempt went."""

    __tablename__ = "sync_state"

    broker: Mapped[str] = mapped_column(String(20), primary_key=True)
    account_number: Mapped[str] = mapped_column(String(40), primary_key=True)
    synced_through: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    """A value saved on the Settings page. What can be set, and each default,
    is in core/settings.py; a setting with no row uses its default."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class UserAlert(Base):
    """An alert the user set (#27): a price, or a trade's open P/L, going
    above or below a level. It goes off once; it shows until dismissed and
    can be set again."""

    __tablename__ = "user_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # "price", "trade_pl", "spread", "bot_pl"
    target: Mapped[str] = mapped_column(String(160))  # a symbol, or a position key
    label: Mapped[str] = mapped_column(String(160))  # what to call it: "SPX", "SPX Iron Condor"
    direction: Mapped[str] = mapped_column(String(10))  # "above" or "below"
    level: Mapped[Decimal] = mapped_column(Money())
    note: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    triggered_value: Mapped[Decimal | None] = mapped_column(Money())
    dismissed: Mapped[bool] = mapped_column(Boolean)
    # A spread's legs: [{"key": OCC symbol, "qty": signed contracts}] (#54)
    legs: Mapped[list | None] = mapped_column(JSON)
    # 0024 (bots slice 9): a bot's default alert: its entry's first run and the rule
    # (one alert per both), and quiet = shown in the app only, never sent.
    bot_run_id: Mapped[int | None] = mapped_column(Integer)
    rule_key: Mapped[str | None] = mapped_column(String(40))
    quiet: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("uq_user_alerts_bot_run_rule", "bot_run_id", "rule_key", unique=True),)


class AccountValue(Base):
    """An account's value at the end of a day (docs/decisions/0023).

    From TastyTrade's own daily history ("history"), or for Schwab, which
    keeps none, the balance read on that day, updated by each later read the
    same day ("daily")."""

    __tablename__ = "account_values"

    broker: Mapped[str] = mapped_column(String(20), primary_key=True)
    account_number: Mapped[str] = mapped_column(String(40), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    net_liquidating_value: Mapped[Decimal] = mapped_column(Money())
    cash: Mapped[Decimal] = mapped_column(Money())
    source: Mapped[str] = mapped_column(String(20))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LatestBalance(Base):
    """Each account's balance as last read from its broker, so the Accounts
    page can show it straight away while it asks for a fresh one (#74)."""

    __tablename__ = "latest_balances"

    broker: Mapped[str] = mapped_column(String(20), primary_key=True)
    account_number: Mapped[str] = mapped_column(String(40), primary_key=True)
    nickname: Mapped[str | None] = mapped_column(String(100))
    account_type: Mapped[str | None] = mapped_column(String(50))
    net_liquidating_value: Mapped[Decimal] = mapped_column(Money())
    cash: Mapped[Decimal] = mapped_column(Money())
    buying_power: Mapped[Decimal] = mapped_column(Money())
    read_at: Mapped[datetime] = mapped_column(UTCDateTime())


# --- Journal: the user's edits and views --------------------------------------


class Tag(Base):
    """A label; a trade can have any number. Trades are filed by tags alone (decision
    0072): a bot files its trades under one (Bot.tag_id)."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)


class TradeEdit(Base):
    """What the user changed on one position, by its stable key. A missing
    row or a null field means "the default"."""

    __tablename__ = "trade_edits"

    position_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    close_reason: Mapped[str | None] = mapped_column(String(40))
    split: Mapped[bool | None] = mapped_column(Boolean)  # iron condor as two spreads
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class TradeTag(Base):
    __tablename__ = "trade_tags"

    position_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class Dashboard(Base):
    """A saved journal view: a name and the filters it applies."""

    __tablename__ = "dashboards"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    sort_order: Mapped[int] = mapped_column(Integer)
    filters: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ChartLayout(Base):
    """A chart grid layout (#25): its shape and panes, in the shape Tracker2's
    chart pages save (payload). The client picks the id. One is active: the
    grid opens with it."""

    __tablename__ = "chart_layouts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ChartTab(Base):
    """An open chart grid tab: a layout and its own pane tickers."""

    __tablename__ = "chart_tabs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    layout_id: Mapped[str] = mapped_column(ForeignKey("chart_layouts.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    symbols: Mapped[dict] = mapped_column(JSON)  # {pane id: ticker}
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ChartDrawing(Base):
    """The lines drawn on one symbol's chart at one timeframe."""

    __tablename__ = "chart_drawings"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    interval: Mapped[str] = mapped_column(String(4), primary_key=True)
    drawings: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WatchlistGroup(Base):
    """A group of symbols in the chart grid's watchlist."""

    __tablename__ = "watchlist_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer)
    collapsed: Mapped[bool] = mapped_column(Boolean, default=False)
    symbols: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ManualBot(Base):
    """A manual bot (0040, #158): named exit rules (profit target, stop loss, exit time,
    alerts) to attach to any open trade. Attaching copies them into a hidden per-trade
    bot, so editing or deleting one never changes trades already using it. Deleting
    sets deleted_at, so run history keeps the name."""

    __tablename__ = "manual_bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    target_value: Mapped[Decimal | None] = mapped_column(Money())
    target_unit: Mapped[str] = mapped_column(String(3), default="pct")  # "pct" or "usd"
    stop_value: Mapped[Decimal | None] = mapped_column(Money())
    stop_unit: Mapped[str] = mapped_column(String(3), default="pct")
    close_time: Mapped[str | None] = mapped_column(String(5))  # HH:MM New York; None: off
    close_dte: Mapped[int] = mapped_column(Integer, default=0)  # days before expiration
    alerts: Mapped[list] = mapped_column(JSON, default=list)  # bot default alert rows
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class PendingManual(Base):
    """A pending attachment (0041, #158): the Trade ticket sent with Manage with a manual
    bot. The bot's rules are copied here at Send (rules), so editing the manual bot later
    doesn't change them; the monitor attaches them to the trade its order opens, once it
    fills, and grows them with later fills of the rest (manual_attach.pending_pass)."""

    __tablename__ = "pending_manual"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(String(20))
    account_number: Mapped[str] = mapped_column(String(40))
    order_id: Mapped[str] = mapped_column(String(60), unique=True)
    manual_bot_id: Mapped[int | None] = mapped_column(Integer)
    rules: Mapped[dict] = mapped_column(JSON)
    run_id: Mapped[int | None] = mapped_column(Integer)
    attached_contracts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="waiting")  # waiting|attached|ended
    read_misses: Mapped[int] = mapped_column(Integer, default=0)  # get_order failures in a row
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # order seen done, unfilled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Bot(Base):
    """A bot (Phase 6): its trades are filed under one journal tag (#30; decision
    0072, which replaced its strategy). New bots start in preview mode, with size
    limits (0009). What it trades and when lives in the bot_* tables (0030)."""

    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="RESTRICT"))
    broker: Mapped[str] = mapped_column(String(20))
    account_number: Mapped[str] = mapped_column(String(40))
    live: Mapped[bool] = mapped_column(Boolean, default=False)  # False: preview mode
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Decision 0054 (migration 0034): why the app switched this bot off, shown on its page
    # until it's armed again, and alerted once (notice_sent)
    notice: Mapped[str | None] = mapped_column(Text)
    notice_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    max_per_trade: Mapped[int] = mapped_column(Integer)  # 0: blank, worked out (0058)
    max_per_day: Mapped[int] = mapped_column(Integer)
    underlying: Mapped[str] = mapped_column(String(20), default="SPX")
    contracts: Mapped[int] = mapped_column(Integer, default=1)  # per entry
    split_iron_condors: Mapped[bool] = mapped_column(Boolean, default=True)  # 0018
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # deleted, but has runs
    # Manual bots (0040, #158): a hidden copy of a manual bot's rules made for one trade,
    # and the manual bot it came from (kept when that bot is deleted, which only marks it).
    # The database has ON DELETE SET NULL (added in place by 0040); it isn't declared here
    # because SQLite can't report it back for a column added in place, so the models
    # would never match the migrations.
    per_trade: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    manual_bot_id: Mapped[int | None] = mapped_column(ForeignKey("manual_bots.id"))
    max_entries_per_day: Mapped[int | None] = mapped_column(Integer)  # 0016: None = no cap
    max_open_trades: Mapped[int | None] = mapped_column(Integer)
    use_premium_bounds: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_min: Mapped[Decimal | None] = mapped_column(Money())
    premium_max: Mapped[Decimal | None] = mapped_column(Money())
    frequency: Mapped[str] = mapped_column(String(14), default="daily")
    entry_days: Mapped[list] = mapped_column(JSON, default=list)  # weekly: 1 = Monday
    monthly_week: Mapped[int | None] = mapped_column(Integer)
    monthly_weekday: Mapped[int | None] = mapped_column(Integer)
    specific_dates: Mapped[list] = mapped_column(JSON, default=list)
    use_blackout_days: Mapped[bool] = mapped_column(Boolean, default=False)
    blackout_days: Mapped[list] = mapped_column(JSON, default=list)
    stop_co_mode: Mapped[str] = mapped_column(String(10), default="off")  # 0017: off, both, tighter
    stop_co_amount: Mapped[Decimal | None] = mapped_column(Money())  # $ off the entry credit
    # 0019: market filters (Tracker2's columns). Each applies only while its use_* is
    # ticked; unticking keeps the numbers. Percentages are positive % moves.
    use_vix_level: Mapped[bool] = mapped_column(Boolean, default=False)
    vix_min: Mapped[Decimal | None] = mapped_column(Money())
    vix_max: Mapped[Decimal | None] = mapped_column(Money())
    use_vix_overnight: Mapped[bool] = mapped_column(Boolean, default=False)
    vix_on_min_up_pct: Mapped[Decimal | None] = mapped_column(Money())
    vix_on_max_up_pct: Mapped[Decimal | None] = mapped_column(Money())
    vix_on_min_down_pct: Mapped[Decimal | None] = mapped_column(Money())
    vix_on_max_down_pct: Mapped[Decimal | None] = mapped_column(Money())
    use_vix9d_ratio: Mapped[bool] = mapped_column(Boolean, default=False)
    vix9d_ratio_min: Mapped[Decimal | None] = mapped_column(Money())
    vix9d_ratio_max: Mapped[Decimal | None] = mapped_column(Money())
    use_technicals: Mapped[bool] = mapped_column(Boolean, default=False)
    sma_mode: Mapped[str | None] = mapped_column(String(10))  # below, above, compare
    sma_days_a: Mapped[int | None] = mapped_column(Integer)
    sma_days_b: Mapped[int | None] = mapped_column(Integer)
    ema_mode: Mapped[str | None] = mapped_column(String(10))
    ema_minutes_a: Mapped[int | None] = mapped_column(Integer)
    ema_minutes_b: Mapped[int | None] = mapped_column(Integer)
    use_intraday_move: Mapped[bool] = mapped_column(Boolean, default=False)
    intraday_min_up_pct: Mapped[Decimal | None] = mapped_column(Money())
    intraday_max_up_pct: Mapped[Decimal | None] = mapped_column(Money())
    intraday_min_down_pct: Mapped[Decimal | None] = mapped_column(Money())
    intraday_max_down_pct: Mapped[Decimal | None] = mapped_column(Money())
    specific_date_sets: Mapped[list] = mapped_column(JSON, default=list)  # "FOMC", "CPI", "EOM"
    blackout_date_sets: Mapped[list] = mapped_column(JSON, default=list)  # Entry day
    # 0046 (#207): the lists checked on the days after entry through expiration
    blackout_dte_sets: Mapped[list] = mapped_column(JSON, default=list)
    # 0020: the timed close (the time is the switch: None is off) and time actions,
    # each {"type", "days", "at", "stop_pct", "target_pct"} with the %s as text.
    close_time: Mapped[str | None] = mapped_column(String(5))  # HH:MM New York
    close_dte: Mapped[int] = mapped_column(Integer, default=0)
    use_time_actions: Mapped[bool] = mapped_column(Boolean, default=False)
    time_actions: Mapped[list] = mapped_column(JSON, default=list)
    # 0021 (bots slice 8): your own labels; archived (never fires, refused while
    # anything is open); the exported_at of the file it was imported from.
    tags: Mapped[list] = mapped_column(JSON, default=list)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    imported_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # 0035: the link it was imported from (decision 0056); None for a file
    imported_from_url: Mapped[str | None] = mapped_column(String(2000))
    # 0023 (bots slice 7): watch rules for the whole trade (Tracker2's columns), each
    # only while its use_* is ticked; a blank number is that part off. Deltas are
    # whole numbers (65, -65), the moves positive %, the tested points signed.
    use_delta_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    position_delta_below: Mapped[Decimal | None] = mapped_column(Money())
    position_delta_above: Mapped[Decimal | None] = mapped_column(Money())
    use_sl_ratio_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    sl_ratio_below: Mapped[Decimal | None] = mapped_column(Money())
    sl_ratio_above: Mapped[Decimal | None] = mapped_column(Money())
    use_underlying_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    underlying_up_pct: Mapped[Decimal | None] = mapped_column(Money())
    underlying_down_pct: Mapped[Decimal | None] = mapped_column(Money())
    short_put_tested_pts: Mapped[Decimal | None] = mapped_column(Money())
    short_call_tested_pts: Mapped[Decimal | None] = mapped_column(Money())
    # 0024 (bots slice 9): the alert name (line 1 of a phone alert), where routine
    # alerts go ("all", "discord", "telegram"), and the default alerts, each
    # {"type", "direction", "value", "points"} with the numbers as text.
    alert_name: Mapped[str | None] = mapped_column(String(40))
    alert_channel: Mapped[str] = mapped_column(String(10), default="all")
    use_default_alerts: Mapped[bool] = mapped_column(Boolean, default=False)
    default_alerts: Mapped[list] = mapped_column(JSON, default=list)
    default_alerts_in_app_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Slice 10 (0025): a floating entry window (HH:MM New York) instead of entry times.
    use_floating_entry: Mapped[bool] = mapped_column(Boolean, default=False)
    floating_start: Mapped[str | None] = mapped_column(String(5))
    floating_end: Mapped[str | None] = mapped_column(String(5))
    # Slice 14 (0028; Tracker2's use_exact_dte): a leg's DTE listed exactly, or the next.
    use_exact_dte: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # Slice 19 (0031): sizing by "contracts" or "dollars" (Tracker2's allocation_type,
    # max_allocation_per_trade), the min short width (points; null off) and whether a
    # strike that's taken or held the other way moves one strike out.
    sizing: Mapped[str] = mapped_column(String(10), default="contracts",
                                        server_default="contracts")  # fmt: skip
    max_per_entry_usd: Mapped[Decimal | None] = mapped_column(Money())
    min_short_width: Mapped[Decimal | None] = mapped_column(Money())
    move_conflicted_strikes: Mapped[bool] = mapped_column(Boolean, default=False,
                                                          server_default=false())  # fmt: skip
    keep_trying_minutes: Mapped[int | None] = mapped_column(Integer)  # #314 (0056)
    # #315 (0057): each one's switch; off keeps the number but doesn't use it
    use_min_short_width: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    use_keep_trying: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BotLeg(Base):
    """One leg of a bot's trade, in form order. How its strike is picked: by
    premium (target $), by offset (target points) from another leg, and from slice 10
    by delta (target |delta|, 0.16) or spot (target points from the SPX price)."""

    __tablename__ = "bot_legs"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(4))  # "buy" or "sell"
    right: Mapped[str] = mapped_column(String(4))  # "call" or "put"
    ratio: Mapped[int] = mapped_column(Integer, default=1)
    dte: Mapped[int] = mapped_column(Integer, default=0)
    pick: Mapped[str] = mapped_column(String(10))  # "premium", "offset", "delta" or "spot"
    target: Mapped[Decimal | None] = mapped_column(Money())
    linked_position: Mapped[int | None] = mapped_column(Integer)  # offset legs
    exact: Mapped[bool] = mapped_column(Boolean, default=True)
    group_position: Mapped[int] = mapped_column(Integer)
    # Slice 19 (0031): the leg's delta exit (whole numbers, position-signed; read while
    # Use delta is ticked) and a linked premium wing's farthest offset from its short.
    exit_delta_below: Mapped[Decimal | None] = mapped_column(Money())
    exit_delta_above: Mapped[Decimal | None] = mapped_column(Money())
    max_offset: Mapped[Decimal | None] = mapped_column(Money())
    fallback_target: Mapped[Decimal | None] = mapped_column(Money())  # #314 (0056)


class BotLegGroup(Base):
    """A side of the trade (a short and its wing) with its own exits."""

    __tablename__ = "bot_leg_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    # The stop amount, read by stop_basis: % above the short's fill (leg_premium),
    # % of the entry credit (entry_premium), or $ off it (closing_order).
    stop_pct: Mapped[Decimal | None] = mapped_column(Money())
    target_pct: Mapped[Decimal | None] = mapped_column(Money())
    stop_basis: Mapped[str] = mapped_column(String(20), default="leg_premium")  # 0017
    stop_unit: Mapped[str] = mapped_column(String(3), default="pct")  # "pct" or "usd"
    stop_usd: Mapped[Decimal | None] = mapped_column(Money())
    target_unit: Mapped[str] = mapped_column(String(3), default="pct")
    target_usd: Mapped[Decimal | None] = mapped_column(Money())
    on_stop_remainder: Mapped[str] = mapped_column(String(20), default="close_market")
    on_stop_remainder_limit: Mapped[Decimal | None] = mapped_column(Money())
    # 0023: profit actions, [{"at", "close_pct", "stop_pct"}] with the numbers as text
    use_profit_actions: Mapped[bool] = mapped_column(Boolean, default=False)
    profit_actions: Mapped[list] = mapped_column(JSON, default=list)
    # 0032 (bots slice 21, Tracker2's on_stop_close_group_id): when this group's resting stop
    # fills, also close the group at this position (None: none).
    on_stop_close_group: Mapped[int | None] = mapped_column(Integer)
    # 0033 (decision 0048): "stop" or "target", the exit that rests at TastyTrade
    broker_exit: Mapped[str] = mapped_column(String(6), default="stop", server_default="stop")


class BotEntryTime(Base):
    __tablename__ = "bot_entry_times"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    # "HH:MM", or "HH:MM:SS" on a 15-second step, New York. SQLite never enforces the 5,
    # and widening it would rebuild the table (decision 0051).
    time: Mapped[str] = mapped_column(String(5))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quantity: Mapped[int | None] = mapped_column(Integer)  # 0016: None = the bot's contracts
    label: Mapped[str | None] = mapped_column(String(50))


class BotChase(Base):
    """How an order is chased: entry orders, and target closes (exit)."""

    __tablename__ = "bot_chase"

    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), primary_key=True)
    phase: Mapped[str] = mapped_column(String(5), primary_key=True)  # "entry" or "exit"
    start_offset: Mapped[Decimal] = mapped_column(Money())
    step: Mapped[Decimal] = mapped_column(Money())
    interval_seconds: Mapped[int] = mapped_column(Integer)
    max_tries: Mapped[int] = mapped_column(Integer)
    # The exit row only (migration 0018): what a close does after its last try
    # ("give_up" or "natural_then_leg_out") and the natural window in minutes.
    exhaust_mode: Mapped[str] = mapped_column(String(30), default="give_up")
    natural_minutes: Mapped[int] = mapped_column(Integer, default=2)
    # Slice 19 (0031): the entry row's time limit in minutes (null: none), and the exit
    # row's switch to market after the last try (one-leg groups only).
    window_minutes: Mapped[int | None] = mapped_column(Integer)
    switch_to_market: Mapped[bool] = mapped_column(Boolean, default=False,
                                                   server_default=false())  # fmt: skip
    # 0039: the exit row's "Leave long legs with no bid to expire" (decision 0065).
    ignore_bidless_longs: Mapped[bool] = mapped_column(Boolean, default=False,
                                                       server_default=false())  # fmt: skip


class BotRun(Base):
    """One firing of one entry time (or a Preview now). The unique key makes a
    double fire impossible (Tracker2's idempotency key)."""

    __tablename__ = "bot_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    # An entry time's HH:MM:SS fits: SQLite never enforces the 5, and rebuilding this
    # table would cascade-delete the rows that point at it (decision 0051)
    entry_time: Mapped[str] = mapped_column(String(5))
    status: Mapped[str] = mapped_column(String(10))
    # pending, skipped, preview, running, filled, partial, missed, error
    live: Mapped[bool] = mapped_column(Boolean)
    account_number: Mapped[str | None] = mapped_column(String(40))  # the bot's, at the claim
    # 0026: the bot's broker at the claim; the run's exits trade there (bots slice 11)
    broker: Mapped[str] = mapped_column(String(20), server_default="tastytrade")
    reason: Mapped[str | None] = mapped_column(Text)
    legs: Mapped[list] = mapped_column(JSON, default=list)  # picked legs: symbol, strike, ...
    target_credit: Mapped[Decimal | None] = mapped_column(Money())
    fill_credit: Mapped[Decimal | None] = mapped_column(Money())
    filled_legs: Mapped[list] = mapped_column(JSON, default=list)
    broker_order_id: Mapped[str | None] = mapped_column(String(60), index=True)
    contracts: Mapped[int] = mapped_column(Integer, default=0)
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_runs.id", ondelete="SET NULL")
    )
    chase_log: Mapped[list] = mapped_column(JSON, default=list)
    filled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # 0023: the underlying's price when the strikes were picked (the underlying move's base)
    entry_spot: Mapped[Decimal | None] = mapped_column(Money())
    # 0029 (bots slice 17, Tracker2's run.sizing): dollars a point (a futures series' own,
    # /ES 50; 100 for every other option), the futures contract it trades (/ESZ6), whether
    # it places its profit target only (a futures run: no stop), and how its entry was
    # sent ("chase", or "limit": a resting limit from the Trade page)
    multiplier: Mapped[Decimal] = mapped_column(Money(), server_default="100", default=100)
    contract: Mapped[str | None] = mapped_column(String(20))
    pt_only: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    # 0033 (decision 0048): the entry's side and price at the mid, and what each group would
    # set there ("Priced as a debit of 1.80. Call spread: target 2.70 rests at TastyTrade; …")
    priced: Mapped[str | None] = mapped_column(Text)
    entry_mode: Mapped[str] = mapped_column(String(10), server_default="chase", default="chase")
    # 0037 (decision 0061): a Paper run trades pretend fills at the mids, never the broker
    paper: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class BotTradeMatch(Base):
    """A past journal trade matched to a bot by hand (#169; Tracker2's BotPosition
    link, without a run): the trade belongs to the bot. Keyed by the position key the
    user ticked; a condor split on match is linked by its whole key and its halves
    resolve through split_from, as a run's link does."""

    __tablename__ = "bot_trade_matches"

    position_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    matched_at: Mapped[datetime] = mapped_column(UTCDateTime())


class BotAutoSkip(Base):
    """A trade a sync never matches to a bot on its own again (#276): you unlinked it,
    or undid its match. Match trades and Match all bots still offer it."""

    __tablename__ = "bot_auto_skips"

    position_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    skipped_at: Mapped[datetime] = mapped_column(UTCDateTime())


class PaperFill(Base):
    """One pretend fill of a Paper run: its entry, an exit, or an expiry (decision 0061)."""

    __tablename__ = "paper_fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # "entry", "exit" or "expiry"
    group: Mapped[int | None] = mapped_column(Integer)  # the leg group closed; None: the trade
    # Per contract: a credit is positive, a debit negative
    price: Mapped[Decimal] = mapped_column(Money())
    contracts: Mapped[int] = mapped_column(Integer)
    # "stop", "target", "timed close", "expired", "switched to Live", ...
    reason: Mapped[str] = mapped_column(String(60))
    legs: Mapped[list] = mapped_column(JSON, default=list)  # leg symbols and the mids used
    at: Mapped[datetime] = mapped_column(UTCDateTime())


class BotExit(Base):
    """One side's exit: its stop, its target, or the wing close after a stop."""

    __tablename__ = "bot_exits"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"), index=True)
    group_position: Mapped[int] = mapped_column(Integer)
    # "stop", "target", "wing", "timed" (a timed close, 0020), "action" (a time action),
    # "close" (a close you asked for from the run page, slice 6, or one a rule started),
    # "watch" (a watch rule that fired on this side) or "profit" (a profit action, slice 7)
    kind: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(10))
    # placing (written, not sent yet), pending (a stop held by Freeze), armed, working,
    # firing (a target or timed close closing), chasing (a wing being chased), filled,
    # cancelled, failed, expired, left (a wing left open after its stop), missed (a
    # timed close the session ended without), reduced (a close you asked for that
    # filled, with part of the side still open)
    short_symbol: Mapped[str] = mapped_column(String(40))
    long_symbol: Mapped[str] = mapped_column(String(40))
    contracts: Mapped[int] = mapped_column(Integer)
    broker_order_id: Mapped[str | None] = mapped_column(String(60))
    trigger: Mapped[Decimal | None] = mapped_column(Money())
    limit: Mapped[Decimal | None] = mapped_column(Money())  # a target: the cost to close at
    fill_price: Mapped[Decimal | None] = mapped_column(Money())
    rearms: Mapped[int] = mapped_column(Integer, default=0)
    rearm_day: Mapped[date | None] = mapped_column(Date)
    log: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # Cancel pressed on a running close (migration 0018); the close's job stops at its next step.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # 0027 (bots slice 13): the legs this exit acts on, per unit of the group
    # ([{symbol, side, ratio}]; a wing row holds the one leg it closes), the leg a resting
    # stop sits on, and whether the app watches the trigger (a group P&L level) instead.
    # short_symbol/long_symbol stay (NOT NULL): the anchor (or "") and the first other leg.
    legs: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    anchor_symbol: Mapped[str | None] = mapped_column(String(40))
    watched: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # 0033 (decision 0048): a stop the app holds on its short (watched on the short's quote)
    # while the group's profit target rests at TastyTrade; false everywhere else
    app_held: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class BotIntegrityAlert(Base):
    """A finding of the integrity checks on a live run (bots slice 8; Tracker2's
    bot_integrity_alerts): alerted once (notified_at), cleared when it goes away so
    a recurrence alerts again."""

    __tablename__ = "bot_integrity_alerts"
    __table_args__ = (UniqueConstraint("run_id", "check_key", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"), index=True)
    check_key: Mapped[str] = mapped_column(String(30))  # wing_missing, stop_without_order, ...
    subject: Mapped[str] = mapped_column(String(40))  # "side 0", "exit 12"
    summary: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cleared_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BotTradeOverride(Base):
    """What you changed on one side of one bot trade from the journal (bots slice 6;
    Tracker2's BotPositionOverride). Written only when a change took effect; the
    exits themselves stay in bot_exits. stop_off and target_off (bots slice 18,
    Tracker2's stop_loss_off and profit_target_off) are what the exit paths check;
    exits_off is both of them (slice 6's single switch)."""

    __tablename__ = "bot_trade_overrides"
    __table_args__ = (UniqueConstraint("run_id", "group_position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"), index=True)
    group_position: Mapped[int] = mapped_column(Integer)
    stop_pct: Mapped[Decimal | None] = mapped_column(Money())  # the % you set; None: unchanged
    target_pct: Mapped[Decimal | None] = mapped_column(Money())
    stop_off: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    target_off: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    log: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")  # each change
    # 0032 (bots slice 21; Tracker2's stop_loss_unit/usd, stop_loss_co_mode/amount,
    # on_stop_remainder/limit, use_profit_actions/profit_actions): this group's own copy of
    # the bot group's setting on this trade. None: the group's own (bots/overrides.py).
    stop_unit: Mapped[str | None] = mapped_column(String(3))  # "pct" or "usd"
    stop_usd: Mapped[Decimal | None] = mapped_column(Money())
    co_mode: Mapped[str | None] = mapped_column(String(10))  # off, both, tighter
    co_amount: Mapped[Decimal | None] = mapped_column(Money())
    on_stop_remainder: Mapped[str | None] = mapped_column(String(20))
    remainder_limit: Mapped[Decimal | None] = mapped_column(Money())
    use_profit_actions: Mapped[bool | None] = mapped_column(Boolean)
    profit_actions: Mapped[list | None] = mapped_column(JSON)  # replaces the group's list

    @hybrid_property
    def exits_off(self) -> bool:
        """Both switched off: slice 6's "exits off" (no stop and no target; since bots
        slice 18 the timed close, watch rules and profit actions still run, as Tracker2)."""
        return bool(self.stop_off) and bool(self.target_off)

    @exits_off.inplace.setter
    def _exits_off_set(self, value: bool) -> None:
        self.stop_off = self.target_off = bool(value)

    @exits_off.inplace.expression
    @classmethod
    def _exits_off_expression(cls):
        return and_(cls.stop_off.is_(True), cls.target_off.is_(True))


class BotRunTimedClose(Base):
    """One trade's own timed close (bots slice 21; Tracker2's BotRunTimedCloseOverride): it
    replaces the bot's close_time/close_dte for that run. The time is the switch (None: no
    timed close for this trade); a None DTE is 0."""

    __tablename__ = "bot_run_timed_closes"
    __table_args__ = (UniqueConstraint("run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"))
    close_time: Mapped[str | None] = mapped_column(String(5))  # HH:MM New York
    close_dte: Mapped[int | None] = mapped_column(Integer)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime())


class BotRunWatchRules(Base):
    """One trade's own watch rules (bots slice 21; Tracker2's BotRunWatchOverride): its delta
    and S/L ratio rules replace the bot's for that run; the underlying rules stay the bot's.
    Whole-number deltas as the bot's; leg_deltas keyed by the trade's leg position (as text):
    {"0": [below, above]}, numbers as text."""

    __tablename__ = "bot_run_watch_rules"
    __table_args__ = (UniqueConstraint("run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"))
    use_delta_exit: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    position_delta_below: Mapped[Decimal | None] = mapped_column(Money())
    position_delta_above: Mapped[Decimal | None] = mapped_column(Money())
    use_sl_ratio_exit: Mapped[bool] = mapped_column(Boolean, default=False,
                                                    server_default=false())  # fmt: skip
    sl_ratio_below: Mapped[Decimal | None] = mapped_column(Money())
    sl_ratio_above: Mapped[Decimal | None] = mapped_column(Money())
    leg_deltas: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime())


class OrderLog(Base):
    """Every order attempt: previews, sends, cancels, replaces and refusals
    (spec: the order path). Attempts, not live status. Today's sends count
    toward a bot's daily limit; an unclear one (result "Unclear: …") blocks its
    legs until cleared_at is set."""

    __tablename__ = "order_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    broker: Mapped[str] = mapped_column(String(20))
    account_number: Mapped[str] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(10))  # "bot", "managed" or "you"
    bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bots.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(10))  # preview, send, cancel, replace, refuse
    opening: Mapped[bool] = mapped_column(Boolean)
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal | None] = mapped_column(Money())
    order_type: Mapped[str] = mapped_column(String(12))
    order: Mapped[dict] = mapped_column(JSON)  # {"order": Order.to_dict(), "body": broker body}
    result: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean)
    broker_order_id: Mapped[str | None] = mapped_column(String(60))
    replaces_id: Mapped[str | None] = mapped_column(String(60))
    tag: Mapped[str | None] = mapped_column(String(40))  # TastyTrade external-identifier
    bot_run_id: Mapped[int | None] = mapped_column(ForeignKey("bot_runs.id", ondelete="SET NULL"))
    cleared_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TickSize(Base):
    """TastyTrade's option increments for a stock ("AAPL"), a futures option
    series ("FOP:ES:EW2Z6") or a futures contract's spreads ("FUT:/ESZ6")."""

    __tablename__ = "tick_sizes"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    sizes: Mapped[list] = mapped_column(JSON)  # [{"threshold": str | None, "value": str}]
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime())


class PLSnapshot(Base):
    """A trade's total P/L (realized + open) after a day's close (#51): the
    baseline the next day's P/L Day is measured from."""

    __tablename__ = "pl_snapshots"

    position_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)  # New York date
    total_pl: Mapped[Decimal] = mapped_column(Money())
    taken_at: Mapped[datetime] = mapped_column(UTCDateTime())


class CloseMark(Base):
    """An option's last real two-sided mark, saved with the day's P/L snapshot
    (#241): after hours and before the open, when its market is pulled, the
    journal marks it here, even after the app restarts."""

    __tablename__ = "close_marks"

    symbol: Mapped[str] = mapped_column(String(80), primary_key=True)
    mark: Mapped[Decimal] = mapped_column(Money())
    saved_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ExpirySettlement(Base):
    """An option settled by the app at its underlying's close on its
    expiration day (#147, decision 0062), until the broker posts its own
    record. One row per account, contract and direction."""

    __tablename__ = "expiry_settlements"
    __table_args__ = (UniqueConstraint("broker", "account_number", "symbol", "sign"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker: Mapped[str] = mapped_column(String(20))
    account_number: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(60))  # OCC
    underlying: Mapped[str] = mapped_column(String(20))
    put_call: Mapped[str] = mapped_column(String(4))
    strike: Mapped[Decimal] = mapped_column(Money())
    expiration: Mapped[date] = mapped_column(Date, index=True)
    sign: Mapped[int] = mapped_column(Integer)  # +1 long, -1 short
    quantity: Mapped[Decimal] = mapped_column(Money())
    close_price: Mapped[Decimal] = mapped_column(Money())  # the underlying's close used
    fee: Mapped[Decimal] = mapped_column(Money())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # "prelim": settled at 4:15 on a quick price, to be corrected; "final": the official close
    status: Mapped[str] = mapped_column(String(8), server_default="final")
    price_source: Mapped[str] = mapped_column(String(40), default="", server_default="")
    prelim_close: Mapped[Decimal | None] = mapped_column(Money())  # the first price, if prelim
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    broker_alerted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AppAlert(Base):
    """An alert the app raises for itself (#186): a settlement at the close, a correction
    to a P/L it showed, a broker's record that differs. Unread until the user opens the
    Alerts page; `dedupe_key` keeps the same event from alerting twice."""

    __tablename__ = "app_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # settled, correction, broker_record, user_alert
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    link: Mapped[str] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class EconomicEvent(Base):
    """A US release on a New York day (0019, Tracker2's economic_events): this
    week's ForexFactory events, and the Fed's FOMC days and FRED's CPI days.
    The bots' date lists read them, and the schedule drawer shows the day's."""

    __tablename__ = "economic_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    event_time: Mapped[str | None] = mapped_column(String(5))  # "HH:MM" New York; None: all day
    title: Mapped[str] = mapped_column(String(120))
    impact: Mapped[str] = mapped_column(String(10))  # "high" or "medium"
    key: Mapped[str | None] = mapped_column(String(10))  # "FOMC", "CPI", or None
    source: Mapped[str] = mapped_column(String(20))  # "forexfactory", "fed", "fred"
    # ForexFactory's figures, for the schedule drawer's tooltips (#221); None when blank
    forecast: Mapped[str | None] = mapped_column(String(20))
    previous: Mapped[str | None] = mapped_column(String(20))


class EconomicEventSource(Base):
    """How each calendar source last refreshed: forexfactory, fed, fred:CPI, nyse."""

    __tablename__ = "economic_event_sources"

    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    refreshed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # last good write
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)


class MarketCalendarDay(Base):
    """A day NYSE is closed, or closes early, from its published calendar (0019).
    Years with rows here replace core/sessions.py's table for those years."""

    __tablename__ = "market_calendar"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))  # "closed" or "early_close"
    close_time: Mapped[str | None] = mapped_column(String(5))  # "HH:MM", early closes
    name: Mapped[str | None] = mapped_column(String(80))  # the holiday, closures


class TradeEvent(Base):
    """A journal trade opening, rolling, partly closing or closing (#178; Tracker2's
    trade_events): what the app's popups show and the Alerts page lists. Recorded after
    a sync, from the journal's trades (services/trade_events.py); `dedupe_key` keeps the
    same fill from making a second one. The trade's name and bot are as they were then.
    `detail` is a close's strikes and rolls (#212), one per line; empty otherwise."""

    __tablename__ = "trade_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(400), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    position_key: Mapped[str] = mapped_column(String(160), index=True)
    name: Mapped[str] = mapped_column(String(200))
    bot_name: Mapped[str | None] = mapped_column(String(200))
    broker: Mapped[str] = mapped_column(String(20))
    account_number: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Money())
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    detail: Mapped[str] = mapped_column(Text, default="", server_default="")


class Idea(Base):
    """A saved Trade ticket to follow (0055, #292). Its P/L is live from the quote hub
    until every leg has expired and has its official close; then final_pnl and final_at
    freeze it and it is never priced again. cards is the ticket as saved (see
    core/ideas.py)."""

    __tablename__ = "ideas"
    __table_args__ = {"sqlite_autoincrement": True}  # a deleted idea's id is never reused

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text)
    cards: Mapped[list] = mapped_column(JSON)
    spot_at_entry: Mapped[Decimal | None] = mapped_column(Money())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    final_pnl: Mapped[Decimal | None] = mapped_column(Money())
    final_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
