"""A bot's definition, as the editor and the engine see it (spec: the first
automation, section 1). Pure: services/bot_store.py reads and writes it from
the bot_* tables. Legs, sides and links refer to each other by position."""

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal

from optionsjourney.trading.orders import Side

# How a leg's strike is picked: by premium ($), offset (points from another leg), and
# from slice 10 by delta (a magnitude, 0.16) or spot (points from the SPX price).
PICKS = ("premium", "offset", "delta", "spot")
RIGHTS = ("call", "put")
FREQUENCIES = ("daily", "weekly", "monthly", "specific_dates")
MAX_CONTRACTS = 100  # contracts per entry, however a bot is sized
MAX_LEGS, MAX_GROUPS, MAX_RATIO = 4, 4, 10  # spec question 3 and 4: what an order can carry
# Bots slice 14 (question 1): a leg's days to expiration. Tracker2 has no limit; the
# passes that look back over held trades read this far plus 10 days.
MAX_DTE = 1100  # LEAPS run past a year; Tracker2 has no cap (#161)
# A group is "single_short" (exactly one sold leg: a stop can rest at the broker) or
# "watched" (anything else: the app watches its price; decision 0039).
GROUP_KINDS = ("single_short", "watched")
# Decision 0048: which of a group's exits rests at the broker (Tracker2's broker_exit); the
# app watches the other (bots/exit_plan.py reads it against the fill).
BROKER_EXITS = ("stop", "target")
# VALID_STOP_LOSS_BASIS, plus "group_entry": % of the group's entry price lost.
STOP_BASES = ("leg_premium", "entry_premium", "closing_order", "group_entry")
RESTING_BASES = ("leg_premium", "entry_premium", "closing_order")  # single_short only
WATCHED_BASES = ("group_entry",)  # watched only
CO_MODES = ("off", "both", "tighter")  # VALID_STOP_LOSS_CO_MODES
EXIT_UNITS = ("pct", "usd")  # VALID_EXIT_UNITS; "usd" is option price per contract
REMAINDERS = ("close_market", "close_limit", "close_chase", "leave")  # on_stop_remainder
# What a close does after its last try (exit_chase.ChaseSettings.exhaust_mode): "give_up"
# cancels it and puts the stop back; "natural_then_leg_out" is Tracker2's EXHAUST_NATURAL;
# "keep_conceding" (EXHAUST_KEEP) steps on, never past the natural, until the session ends.
EXHAUST_MODES = ("give_up", "natural_then_leg_out", "keep_conceding")
SIZINGS = ("contracts", "dollars")  # slice 19: Tracker2's VALID_ALLOCATION_TYPES
WINDOW_MINUTES = (1, 390)  # slice 19: the entry chase's time limit, at most a session
NATURAL_MINUTES = (1, 30)  # the natural window's allowed range, in minutes
# Time actions (Tracker2's time_actions.py): "N days to expiration" or "N days in trade".
TIME_ACTION_TYPES = ("dte", "dit")
MAX_TIME_ACTIONS = 10  # validation.MAX_TIME_ACTIONS
MAX_PROFIT_ACTIONS = 5  # validation.MAX_PROFIT_ACTIONS, per side
# Slice 9: default alerts (Tracker2's validation.MAX_DEFAULT_ALERTS, DEFAULT_ALERT_TYPES),
# the alert name's length (bots.alert_name) and where routine alerts go.
MAX_DEFAULT_ALERTS = 10
DEFAULT_ALERT_TYPES = ("pnl_pct", "strike_offset")
ALERT_NAME_MAX = 40
ALERT_CHANNELS = ("all", "discord", "telegram")
# Tags (Tracker2's automation_bp._normalize_tags): free labels for filing bots. The
# state words are reserved: the list offers them as filters, worked out from the bot.
TAG_MAX_LEN = 40
MAX_TAGS = 20
RESERVED_TAGS = ("Armed", "Off", "Live", "Paper")


@dataclass(frozen=True)
class MoveLimits:
    """Option Omega's four move fields, in % (positive numbers, None = no limit):
    the VIX overnight move and the underlying's intraday move (Tracker2's
    vix_on_*_pct and intraday_*_pct columns)."""

    min_up: Decimal | None = None
    max_up: Decimal | None = None
    min_down: Decimal | None = None
    max_down: Decimal | None = None

    @property
    def any_set(self) -> bool:
        return any(v is not None for v in (self.min_up, self.max_up, self.min_down, self.max_down))


@dataclass(frozen=True)
class EntryTimeDef:
    """One entry time: "HH:MM" New York, on or paused, with an optional
    contracts override for this time and a short label."""

    time: str
    enabled: bool = True
    quantity: int | None = None  # None: use the bot's contracts
    label: str | None = None


@dataclass(frozen=True)
class LegDef:
    position: int
    side: Side
    right: str  # "call" or "put"
    ratio: int = 1
    dte: int = 0
    pick: str = "premium"  # one of PICKS: target $, points from `link`, |delta| or points
    target: Decimal | None = None
    link: int | None = None  # the leg an offset is measured from
    exact: bool = True  # offset: that exact strike, or skip
    group: int = 0  # the side it belongs to
    # Slice 19: the leg's delta exit (Tracker2's BotLeg.exit_delta_below/above): whole
    # numbers, position-signed, read only while Use delta is ticked; None: that part off.
    exit_delta_below: Decimal | None = None
    exit_delta_above: Decimal | None = None
    # Slice 19: a bought premium leg linked to a sold one (`link`) sits no further than
    # this many points from it (BotLeg.max_strike_offset; puts negative, calls positive).
    max_offset: Decimal | None = None


@dataclass(frozen=True)
class DefaultAlertDef:
    """One default alert rule (Tracker2's bots.default_alerts): "pnl_pct" goes off
    when the trade's P&L goes above or below `value` % of its credit;
    "strike_offset" when SPX comes within `points` of a sold strike."""

    type: str
    direction: str | None = None  # pnl_pct: "above" or "below"
    value: Decimal | None = None  # pnl_pct: the P&L %, signed
    points: Decimal | None = None  # strike_offset: 0 or more


@dataclass(frozen=True)
class ProfitActionDef:
    """One profit action of a side (Tracker2's profit_actions.Action, per leg group):
    once the side's P&L reaches `at` (in the side's target unit: % of its credit, or $
    profit per contract), close `close_pct` % of its original contracts (0: none)
    and/or move its stop to `stop_pct`, a P&L level as a % loss of the side's credit
    (0 = break-even, negative keeps that much of the credit; None: the stop stays)."""

    at: Decimal
    close_pct: int = 0
    stop_pct: Decimal | None = None
    # Slice 19 (Tracker2's adjust_sl_usd, "$ P&L"): the new stop as a P&L level per
    # contract in option price, + keeps that much profit. At most one of the two is set.
    stop_usd: Decimal | None = None

    @property
    def sig(self) -> str:
        """What the action says, not where it sits (as TimeActionDef.sig): editing a
        live bot's rows never runs a done action again or skips the next one. A $ stop
        adds "|$L", so a row saved before slice 19 keeps its sig."""
        stop = "" if self.stop_pct is None else _pct_text(self.stop_pct)
        sig = f"{_pct_text(self.at)}|{self.close_pct}|{stop}"
        return sig if self.stop_usd is None else f"{sig}|${_pct_text(self.stop_usd)}"

    @property
    def moves_stop(self) -> bool:
        return self.stop_pct is not None or self.stop_usd is not None

    def label(self, unit: str = "pct") -> str:
        level = f"${self.at:f}" if unit == "usd" else f"{_pct_text(self.at)}%"
        parts = [f"close {self.close_pct}%"] if self.close_pct else []
        if self.stop_pct is not None:
            parts.append(f"stop to {_pct_text(self.stop_pct)}%")
        if self.stop_usd is not None:
            parts.append(f"stop to ${_pct_text(self.stop_usd)} P&L")
        return f"at {level}: {', '.join(parts)}"


@dataclass(frozen=True)
class GroupDef:
    """A set of legs managed together, with its own exits (Tracker2's BotLegGroup)."""

    position: int
    name: str
    # The stop amount, read by stop_basis (Tracker2 reuses stop_loss_pct the same way):
    # leg_premium: % above the short's fill; entry_premium: % of the whole entry
    # credit; closing_order: dollars off the whole entry credit.
    stop_pct: Decimal | None = None
    target_pct: Decimal | None = None  # close when this % of the side's credit is kept
    stop_basis: str = "leg_premium"
    stop_unit: str = "pct"  # "usd": stop_usd, the loss per contract in option price
    stop_usd: Decimal | None = None
    target_unit: str = "pct"  # "usd": target_usd, the profit per contract in option price
    target_usd: Decimal | None = None
    on_stop_remainder: str = "close_market"  # the wing after a stop: one of REMAINDERS
    remainder_limit: Decimal | None = None  # close_limit's price; None: the live bid
    # Slice 7: profit actions, fired in order by the watch pass while ticked.
    use_profit_actions: bool = False
    profit_actions: tuple[ProfitActionDef, ...] = ()
    # Slice 21 (Tracker2's on_stop_close_group_id): when this group's resting stop fills,
    # also close the group at this position (None: none; validation._pairing).
    close_on_stop: int | None = None
    # Decision 0048: "stop" or "target", the exit that rests at TastyTrade; the app watches
    # the other. Read against the trade's fill by exit_plan.plan_group.
    broker_exit: str = "stop"

    @property
    def stop_value(self) -> Decimal | None:
        """The stop in force: the $ amount or the % amount, by unit (exits.exits_config)."""
        return self.stop_usd if self.stop_unit == "usd" else self.stop_pct

    @property
    def target_value(self) -> Decimal | None:
        return self.target_usd if self.target_unit == "usd" else self.target_pct


def _pct_text(value: Decimal | None) -> str:
    """A % as written, without trailing zeros: 50.0 and 50 read the same."""
    if value is None:
        return ""
    d = value.normalize()
    return f"{d.quantize(Decimal(1)) if d == d.to_integral() else d:f}"


@dataclass(frozen=True)
class TimeActionDef:
    """One time action (Tracker2's time_actions.Row): at `days` DTE or days in trade,
    at `at` (HH:MM New York), move each open side's stop and/or target to a new %,
    and/or close `close_pct` % of its original contracts (slice 7)."""

    type: str  # "dte" or "dit"
    days: int
    at: str
    stop_pct: Decimal | None = None  # the side's stop, re-placed at this %
    target_pct: Decimal | None = None  # the side's target: this % of its credit kept
    close_pct: int | None = None  # slice 7: close this % of the side's original contracts

    @property
    def label(self) -> str:
        moves = [f"stop {_pct_text(self.stop_pct)}%"] if self.stop_pct is not None else []
        if self.target_pct is not None:
            moves.append(f"target {_pct_text(self.target_pct)}%")
        if self.close_pct:
            moves.append(f"close {self.close_pct}%")
        return f"{self.days} {self.type.upper()} @ {self.at}: {', '.join(moves)}"

    @property
    def sig(self) -> str:
        """What the row says, not where it sits (Row.sig): editing a live bot's rows
        never makes a fired row fire again."""
        sig = "|".join(
            (self.type, str(self.days), self.at, _pct_text(self.stop_pct),
             _pct_text(self.target_pct))
        )  # fmt: skip
        # Only a row with a close says so: rows saved before slice 7 keep their sig.
        return f"{sig}|close {self.close_pct}" if self.close_pct else sig


@dataclass(frozen=True)
class ChaseDef:
    start_offset: Decimal
    step: Decimal
    interval_seconds: int
    max_tries: int
    # The exit chase only: what a close does after its last try, and the natural window.
    exhaust_mode: str = "give_up"
    natural_minutes: int = 2
    # Slice 19: the entry chase stops after this many minutes (entry_window_minutes;
    # None: only its tries), and a one-leg close goes to market after its last try
    # (switch_to_market_after_max_attempts). Each is read on its own row only.
    window_minutes: int | None = None
    switch_to_market: bool = False
    # The exit chase only (decision 0065): a close leaves out a bought leg with no bid
    # (it expires) instead of selling it; sold legs are always closed.
    ignore_bidless_longs: bool = False


DEFAULT_ENTRY_CHASE = ChaseDef(Decimal("0"), Decimal("0.05"), 10, 6)
# New bots get Tracker2's default ending; bots saved before slice 3 keep "give_up".
DEFAULT_EXIT_CHASE = ChaseDef(Decimal("0"), Decimal("0.05"), 5, 6, "natural_then_leg_out", 2)


@dataclass(frozen=True)
class BotDef:
    id: int | None
    name: str
    tag_id: int | None  # the journal tag its trades are filed under (decision 0072)
    broker: str
    account_number: str
    underlying: str = "SPX"
    contracts: int = 1
    live: bool = False
    enabled: bool = False
    # Decision 0058: None (blank) is worked out from the bot (trade_cap, day_cap)
    max_per_trade: int | None = None
    max_per_day: int | None = None
    split_iron_condors: bool = True
    legs: tuple[LegDef, ...] = ()
    groups: tuple[GroupDef, ...] = ()
    entry_times: tuple[EntryTimeDef, ...] = ()
    entry_chase: ChaseDef = field(default=DEFAULT_ENTRY_CHASE)
    exit_chase: ChaseDef = field(default=DEFAULT_EXIT_CHASE)
    max_entries_per_day: int | None = None
    max_open_trades: int | None = None
    use_premium_bounds: bool = False
    premium_min: Decimal | None = None
    premium_max: Decimal | None = None
    frequency: str = "daily"
    entry_days: tuple[int, ...] = ()  # weekly: 1 = Monday ... 7 = Sunday
    monthly_week: int | None = None  # monthly: 1 = first ... 5 = fifth
    monthly_weekday: int | None = None  # monthly: 1 = Monday ... 5 = Friday
    specific_dates: tuple[str, ...] = ()  # "YYYY-MM-DD"
    use_blackout_days: bool = False
    blackout_days: tuple[str, ...] = ()  # "YYYY-MM-DD"
    stop_co_mode: str = "off"  # the closing-order overlay: one of CO_MODES
    stop_co_amount: Decimal | None = None  # dollars off the whole entry credit
    # Market filters (slice 5; Tracker2's Bot columns). Each applies only while its
    # use_* box is ticked; unticked, its numbers are kept but ignored.
    use_vix_level: bool = False
    vix_min: Decimal | None = None
    vix_max: Decimal | None = None
    use_vix_overnight: bool = False
    vix_overnight: MoveLimits = MoveLimits()
    use_vix9d_ratio: bool = False
    vix9d_ratio_min: Decimal | None = None
    vix9d_ratio_max: Decimal | None = None
    use_technicals: bool = False
    sma_mode: str | None = None  # "below", "above", "compare"; None = off
    sma_days_a: int | None = None
    sma_days_b: int | None = None  # compare only
    ema_mode: str | None = None
    ema_minutes_a: int | None = None
    ema_minutes_b: int | None = None
    use_intraday_move: bool = False
    intraday: MoveLimits = MoveLimits()
    # Named date lists ("FOMC", "CPI", "EOM", "HOLIDAY") added to specific dates / blackout
    # days. Each blackout list has two checks (#207, decision 0083): blackout_date_sets skip
    # an entry on the list's day (Entry day), blackout_dte_sets one whose trade would be
    # open over a later list day through its expiration (Before expiration).
    specific_date_sets: tuple[str, ...] = ()
    blackout_date_sets: tuple[str, ...] = ()
    blackout_dte_sets: tuple[str, ...] = ()
    # Slice 4 (Tracker2's scheduled_close_time / _dte and time actions). The close
    # time is the on/off switch: None is no timed close.
    close_time: str | None = None  # HH:MM New York
    close_dte: int = 0  # close on the day the trade is this many days or fewer from expiring
    use_time_actions: bool = False
    time_actions: tuple[TimeActionDef, ...] = ()
    # Slice 8: your own labels (never shared), and whether the bot is archived (it
    # never fires; archive and unarchive are their own actions, never a save).
    tags: tuple[str, ...] = ()
    archived: bool = False
    # Slice 7: watch rules for the whole trade (Tracker2's Bot columns). Each applies
    # only while its use_* box is ticked; a blank number is that part off. Deltas are
    # whole numbers like Option Omega's (65, -65); the % moves are positive.
    use_delta_exit: bool = False
    position_delta_below: Decimal | None = None
    position_delta_above: Decimal | None = None
    use_sl_ratio_exit: bool = False
    sl_ratio_below: Decimal | None = None
    sl_ratio_above: Decimal | None = None
    use_underlying_exit: bool = False
    underlying_up_pct: Decimal | None = None
    underlying_down_pct: Decimal | None = None
    short_put_tested_pts: Decimal | None = None  # points out of the money (negative: past)
    short_call_tested_pts: Decimal | None = None
    # Slice 9: alerts. The alert name and channel are yours (never shared); the
    # default alerts are made when a live entry fills (Tracker2's bots columns).
    alert_name: str | None = None  # line 1 of a phone alert; None: the bot's name
    alert_channel: str = "all"  # where routine alerts go: one of ALERT_CHANNELS
    use_default_alerts: bool = False
    default_alerts: tuple[DefaultAlertDef, ...] = ()
    default_alerts_in_app_only: bool = False
    # Slice 10: a floating entry window (Tracker2's use_floating_entry): checked every
    # minute from start to end (HH:MM New York, both included) instead of entry times.
    use_floating_entry: bool = False
    floating_start: str | None = None
    floating_end: str | None = None
    # Slice 14 (Tracker2's use_exact_dte): a leg's DTE must be listed exactly; off, the
    # first listed expiration at or after it (bots/expirations.py).
    use_exact_dte: bool = True
    # Slice 19 (Tracker2's allocation_type / max_allocation_per_trade): "contracts" sizes
    # each entry by its contracts; "dollars" by the most buying power an entry may use,
    # what the broker says one unit needs on its own at the worst price the entry chase
    # can reach, credit or debit (decision 0050; bots/sizing.py).
    sizing: str = "contracts"
    max_per_entry_usd: Decimal | None = None
    # Slice 19: the shorts' least width in points (resolver._enforce_min_short_width;
    # None off), and moving a taken or held-against strike one out (resolve_conflicts).
    min_short_width: Decimal | None = None
    move_conflicted_strikes: bool = False
    # Manual bots (#158): a manual bot's rules copied onto one trade. It never enters
    # (no entry times), is never listed, and only manages the trade it was made for.
    per_trade: bool = False

    @property
    def market_picks(self) -> bool:
        """Some leg picks its strike from market data that must be fresh (delta, spot)."""
        return any(leg.pick in ("delta", "spot") for leg in self.legs)

    def legs_in(self, group: int) -> tuple[LegDef, ...]:
        return tuple(leg for leg in self.legs if leg.group == group)

    def kind(self, position: int) -> str:
        """The kind of the group at `position`, from its legs (group_kind)."""
        return group_kind(leg.side for leg in self.legs_in(position))

    def rests_stop(self, position: int) -> bool:
        """The group at `position` can rest its stop at the broker (rests_stop)."""
        return rests_stop(leg.side for leg in self.legs_in(position))


def group_kind(sides: Iterable[Side | str]) -> str:
    """A group with exactly one sold leg is "single_short": on a credit fill its stop is
    worked out on that short, and rests there unless the group chose its target (decision
    0048, exit_plan.plan_group). Any other group is "watched": its stop is a P&L level the
    app watches (decision 0039)."""
    sells = sum(1 for s in sides if Side(s) is Side.SELL)
    return "single_short" if sells == 1 else "watched"


def rests_stop(sides: Iterable[Side | str]) -> bool:
    """Decision 0054: only a group of exactly one leg, a sold one, may choose to rest its
    stop at the broker. Any other group rests its target (broker_exit "target", set where
    a bot comes in: the form, a bot file, migration 0034) and the app watches its stop,
    measured as its kind says (on its short's price for a single_short spread)."""
    return [Side(s) for s in sides] == [Side.SELL]


def lone_shorts_rest_stops(bot: BotDef) -> BotDef:
    """Decision 0054, where a bot comes in (the form, a bot file): a group that can't rest
    its stop (more than one leg) rests its target instead of being refused."""
    groups = tuple(
        g if g.broker_exit != "stop" or bot.rests_stop(g.position)
        else replace(g, broker_exit="target")
        for g in bot.groups
    )  # fmt: skip
    return replace(bot, groups=groups)


def holds_overnight(bot: BotDef) -> bool:
    """Some leg expires after today: the trade is held past the close (slice 14)."""
    return any(leg.dte > 0 for leg in bot.legs)


def largest_ratio(bot: BotDef) -> int:
    """The biggest leg ratio, 1 with no legs: a bot order counts contracts times this
    against the limits (Tracker2's scale_legs; check.py)."""
    return max((leg.ratio for leg in bot.legs), default=1)


def trade_cap(bot: BotDef) -> int:
    """Most contracts per trade, counted in the largest leg (decision 0058): the bot's
    own limit, or when blank the biggest entry it makes by itself: its contracts or an
    entry time's own count, times the largest ratio. Sized by buying power, the
    contract ceiling (MAX_CONTRACTS) times the ratio."""
    if bot.max_per_trade is not None:
        return bot.max_per_trade
    return max(_entry_sizes(bot)) * largest_ratio(bot)


def day_cap(bot: BotDef) -> int:
    """Most contracts per day, counted the same way (decision 0058): the bot's own
    limit, or when blank what its entries add up to in a day: each entry time that's
    on (the window: one entry), the biggest Most entries per day of them, and never
    less than one entry."""
    if bot.max_per_day is not None:
        return bot.max_per_day
    per_entry = sorted(_entry_sizes(bot, each=True), reverse=True)
    if bot.max_entries_per_day is not None:
        per_entry = per_entry[: bot.max_entries_per_day]
    return max(sum(per_entry) * largest_ratio(bot), trade_cap(bot))


def limits(bot: BotDef, by_hand: bool) -> tuple[int, int | None]:
    """(most per trade, most per day or None for no daily cap) for one of the bot's
    entries. `by_hand`: one you start yourself (Trade now, the Trade page, taking a
    trade over): a blank limit doesn't hold it to the bot's own size, only to the
    contract ceiling, and leaves it out of the worked-out day."""
    if not by_hand:
        return trade_cap(bot), day_cap(bot)
    per_trade = bot.max_per_trade or MAX_CONTRACTS * largest_ratio(bot)
    return per_trade, bot.max_per_day


def _entry_sizes(bot: BotDef, each: bool = False) -> list[int]:
    """Contracts of each entry the bot makes by itself in a day (`each`), or of each
    kind of entry it can make."""
    if bot.sizing == "dollars":
        size = MAX_CONTRACTS
        times = [] if bot.use_floating_entry else [t for t in bot.entry_times if t.enabled]
        return [size] * max(1, len(times)) if each else [size]
    base = max(bot.contracts, 1)
    if bot.use_floating_entry:  # once a day, at the bot's contracts
        return [base]
    sizes = [t.quantity or base for t in bot.entry_times if t.enabled]
    if each:
        return sizes or [base]
    return [base, *sizes]


def is_iron_condor(bot: BotDef) -> bool:
    """Four ratio-1 legs in two groups: a sold and a bought put, and a sold and a
    bought call (core/positions splits a condor by type; hand-over reads this)."""
    if len(bot.legs) != 4 or len(bot.groups) != 2 or any(leg.ratio != 1 for leg in bot.legs):
        return False
    rights = []
    for group in bot.groups:
        legs = bot.legs_in(group.position)
        if len(legs) != 2 or {leg.side for leg in legs} != {Side.SELL, Side.BUY}:
            return False
        if legs[0].right != legs[1].right:
            return False
        rights.append(legs[0].right)
    return sorted(rights) == ["call", "put"]


def is_iron_condor_shape(bot: BotDef) -> bool:
    """#166 (Tracker2's split_ic): a sold and a bought put and a sold and a bought call,
    all at the same DTE, in any ratios and however they're grouped (#165), as long as no
    group mixes puts and calls. The journal splits such a trade by type into a put spread
    and a call spread (core.structures.splits_by_type, the same rule for a trade)."""
    if len(bot.legs) != 4 or len({leg.dte for leg in bot.legs}) != 1:
        return False
    for group in bot.groups:
        if len({leg.right for leg in bot.legs_in(group.position)}) > 1:
            return False
    pairs = sorted((leg.right, leg.side.value) for leg in bot.legs)
    return pairs == sorted((r, s.value) for r in ("call", "put") for s in (Side.SELL, Side.BUY))


def sides_of(bot: BotDef) -> tuple[tuple[int, ...], ...]:
    """#165: a condor's journal sides as the positions of the groups holding them
    (one group each for a two-group condor, two for one with a group per leg),
    in the order of their first group."""
    by_right: dict[str, set[int]] = {}
    for leg in bot.legs:
        by_right.setdefault(leg.right, set()).add(leg.group)
    return tuple(sorted(tuple(sorted(g)) for g in by_right.values()))


def watched_groups(bot: BotDef) -> tuple[GroupDef, ...]:
    """The groups with a stop the app may watch: no single sold leg (decision 0039), or the
    target chosen to rest (decision 0048; with no target set, the stop is still watched on
    any debit fill). A group that chose Stop is also watched on a trade that fills for a
    debit (the fall-back); the bot's page and the guide say so."""
    return tuple(
        g for g in bot.groups
        if g.stop_value is not None
        and (bot.kind(g.position) == "watched" or g.broker_exit == "target")
    )  # fmt: skip


def app_watched(bot: BotDef) -> bool:
    """The app must keep running for this bot's stops: a watched group with a stop
    (decision 0039), on either broker (decision 0060)."""
    return bool(watched_groups(bot))


def normalize_tags(tags) -> tuple[str, ...]:
    """Tags as stored (_normalize_tags): trimmed, inner spaces collapsed, cut to
    TAG_MAX_LEN, blanks and reserved words dropped, duplicates ignoring case dropped
    (the first spelling wins), sorted ignoring case."""
    reserved = {word.lower() for word in RESERVED_TAGS}
    seen: dict[str, str] = {}
    for tag in tags:
        tag = " ".join(str(tag).split())[:TAG_MAX_LEN].strip()
        if tag and tag.lower() not in seen and tag.lower() not in reserved:
            seen[tag.lower()] = tag
    return tuple(sorted(seen.values(), key=str.lower))


def retag(tags, add=(), remove=()) -> tuple[str, ...]:
    """Bulk tagging (bots slice 20; Tracker2's api_bulk_tag_bots): `remove` dropped
    ignoring case, then `add` put in with the bot's own spelling winning, through
    normalize_tags."""
    gone = {" ".join(str(t).split())[:TAG_MAX_LEN].strip().lower() for t in remove}
    kept = [t for t in tags if t.lower() not in gone]
    return normalize_tags([*kept, *add])


def normalize_delta(value: Decimal | None) -> Decimal | None:
    """validation.normalize_delta: "16", "-16" and "0.16" all mean 0.16; a bare 1 is
    1.00 (100 delta), not 0.01. None for 0, above 100, or nothing."""
    if value is None:
        return None
    size = abs(value)
    if size == 0 or size > 100:
        return None
    return size if size <= 1 else size / 100


def contracts_for(bot: BotDef, hhmm: str) -> tuple[int, int | None]:
    """(contracts to send, the amount asked for if it was cut down, else None)
    for the entry at `hhmm`: that time's own quantity or the bot's contracts,
    never more than the bot's per-trade limit counted in its largest leg."""
    requested = bot.contracts
    for entry in bot.entry_times:
        if entry.time == hhmm and entry.quantity is not None:
            requested = entry.quantity
    sent = min(requested, trade_cap(bot) // largest_ratio(bot))
    return sent, (requested if sent < requested else None)


def iron_condor_skeleton(name: str = "") -> BotDef:
    """A new bot: an iron condor with blank targets for the user to fill in.
    No personal numbers ship in the app."""
    legs = (
        LegDef(0, Side.SELL, "call", pick="premium", group=0),
        LegDef(1, Side.BUY, "call", pick="offset", link=0, group=0),
        LegDef(2, Side.SELL, "put", pick="premium", group=1),
        LegDef(3, Side.BUY, "put", pick="offset", link=2, group=1),
    )
    groups = (GroupDef(0, "Call side"), GroupDef(1, "Put side"))
    return BotDef(None, name, None, "tastytrade", "", legs=legs, groups=groups)


@dataclass(frozen=True)
class ShapeDef:
    """A preset for a new bot's structure: legs and groups with blank targets. A shape
    that prices as a debit rests its targets (decision 0048). Tracker2 has no picker."""

    label: str
    legs: tuple[LegDef, ...]
    groups: tuple[GroupDef, ...]


def _group(position: int, name: str, legs: tuple[LegDef, ...], debit: bool) -> GroupDef:
    """A group named `name`: a debit shape rests its target and stops on its entry price
    lost; a credit one stops on its one short's fill when it has one, and rests that stop
    only when the short is alone (decision 0054); otherwise its target rests."""
    one_short = group_kind(leg.side for leg in legs) == "single_short"
    basis = "leg_premium" if one_short and not debit else "group_entry"
    rests = "stop" if rests_stop(leg.side for leg in legs) and not debit else "target"
    return GroupDef(position, name, stop_basis=basis, broker_exit=rests)


def _one_group(name: str, legs: tuple[LegDef, ...], debit: bool, label: str) -> ShapeDef:
    return ShapeDef(label, legs, (_group(0, name, legs, debit),))


def _buy(pos, right, pick="premium", **kw) -> LegDef:
    return LegDef(pos, Side.BUY, right, pick=pick, **kw)


def _sell(pos, right, pick="premium", **kw) -> LegDef:
    return LegDef(pos, Side.SELL, right, pick=pick, **kw)


def _shapes() -> dict[str, ShapeDef]:
    condor = iron_condor_skeleton()
    # decision 0054: a side of two legs rests its target; the app watches its stop
    sides = tuple(replace(g, broker_exit="target") for g in condor.groups)
    out = {"iron_condor": ShapeDef("Iron condor", condor.legs, sides)}
    for right in ("put", "call"):
        legs = (_sell(0, right), _buy(1, right, "offset", link=0))
        out[f"{right}_credit_spread"] = _one_group(
            f"{right.capitalize()} spread", legs, False, f"{right.capitalize()} credit spread"
        )
    for right in ("put", "call"):
        legs = (_buy(0, right), _sell(1, right, "offset", link=0))
        out[f"{right}_debit_spread"] = _one_group(
            f"{right.capitalize()} spread", legs, True, f"{right.capitalize()} debit spread"
        )
    for right in ("put", "call"):
        out[f"short_{right}"] = _one_group(
            f"Short {right}", (_sell(0, right),), False, f"Short {right}"
        )
    for right in ("put", "call"):
        out[f"long_{right}"] = _one_group(f"Long {right}", (_buy(0, right),), True, f"Long {right}")
    out["iron_butterfly"] = _one_group(
        "Iron butterfly",
        (
            _sell(0, "call", "spot", target=Decimal(0)),
            _sell(1, "put", "spot", target=Decimal(0)),
            _buy(2, "call", "offset", link=0),
            _buy(3, "put", "offset", link=1),
        ),
        False,
        "Iron butterfly",
    )
    out["long_call_butterfly"] = _one_group(
        "Call butterfly",
        (
            _sell(0, "call", ratio=2),
            _buy(1, "call", "offset", link=0),
            _buy(2, "call", "offset", link=0),
        ),
        True,
        "Long call butterfly",
    )
    out["short_straddle"] = _one_group(
        "Straddle",
        (
            _sell(0, "call", "spot", target=Decimal(0)),
            _sell(1, "put", "spot", target=Decimal(0)),
        ),
        False,
        "Short straddle",
    )
    out["short_strangle"] = _one_group(
        "Strangle", (_sell(0, "call"), _sell(1, "put")), False, "Short strangle"
    )
    out["long_straddle"] = _one_group(
        "Straddle",
        (
            _buy(0, "call", "spot", target=Decimal(0)),
            _buy(1, "put", "spot", target=Decimal(0)),
        ),
        True,
        "Long straddle",
    )
    out["condor"] = _one_group(
        "Condor",
        (
            _buy(0, "call"),
            _sell(1, "call", "offset", link=0),
            _sell(2, "call", "offset", link=0),
            _buy(3, "call", "offset", link=0),
        ),
        True,
        "Condor",
    )
    reverse = (
        _buy(0, "put"),
        _sell(1, "put", "offset", link=0),
        _buy(2, "call", group=1),
        _sell(3, "call", "offset", link=2, group=1),
    )
    out["reverse_iron_condor"] = ShapeDef(
        "Reverse iron condor",
        reverse,
        (_group(0, "Put side", reverse[:2], True), _group(1, "Call side", reverse[2:], True)),
    )
    out.update(_held_shapes())
    out["blank"] = _one_group("Group 1", (_sell(0, "call"),), False, "Blank (one leg)")
    return out


# Slice 14: shapes in two expirations. The near leg is sold and the far one bought (a
# group's sold legs never expire after its bought ones); their DTEs are structure, not
# personal numbers, so they're filled in; the targets are left blank as for any shape.
NEAR_DTE, FAR_DTE = 7, 14


def _held_shapes() -> dict[str, ShapeDef]:
    def pair(base: int, right: str, group: int, same_strike: bool) -> tuple[LegDef, LegDef]:
        near = _sell(base, right, dte=NEAR_DTE, group=group)
        far = _buy(base + 1, right, "offset", link=base, dte=FAR_DTE, group=group,
                   target=Decimal(0) if same_strike else None)  # fmt: skip
        return near, far

    out = {}
    for key, label, same in (("calendar", "Calendar", True), ("diagonal", "Diagonal", False)):
        legs = pair(0, "put", 0, same)
        out[key] = _one_group(label, legs, True, label)
    for key, label, same in (
        ("double_calendar", "Double calendar", True),
        ("double_diagonal", "Double diagonal", False),
    ):
        puts, calls = pair(0, "put", 0, same), pair(2, "call", 1, same)
        word = label.split()[-1]
        out[key] = ShapeDef(
            label,
            (*puts, *calls),
            (_group(0, f"Put {word}", puts, True), _group(1, f"Call {word}", calls, True)),
        )
    return out


SHAPES: dict[str, ShapeDef] = _shapes()


def shape(key: str, base: BotDef) -> BotDef:
    """`base` with the shape's legs and groups; every other setting stays as `base` has
    it, except that contract limits you set are raised to at least contracts x the shape's
    largest ratio, so the shape fits them (blank ones are worked out anyway)."""
    chosen = SHAPES[key]
    out = replace(base, legs=chosen.legs, groups=chosen.groups)
    least = out.contracts * largest_ratio(out)
    return replace(
        out,
        max_per_trade=None if out.max_per_trade is None else max(out.max_per_trade, least),
        max_per_day=None if out.max_per_day is None else max(out.max_per_day, least),
    )


# Editing a bot's structure on the form (no JavaScript: each is a submit button that
# redraws the form with what was typed). Positions are renumbered to stay 0..n-1.


def add_leg(bot: BotDef) -> BotDef:
    """A blank sell call in the last group; nothing at MAX_LEGS."""
    if len(bot.legs) >= MAX_LEGS:
        return bot
    group = bot.groups[-1].position if bot.groups else 0
    return replace(bot, legs=(*bot.legs, LegDef(len(bot.legs), Side.SELL, "call", group=group)))


def add_linked_leg(bot: BotDef, position: int) -> BotDef:
    """#115 (Tracker2's addChildLeg): a leg right below the one at `position`, on the
    other side with its type, ratio, DTE and group, an exact offset 5 points out from it
    (puts below, calls above). Nothing at MAX_LEGS, or from an offset or a linked wing,
    which can't be measured from."""
    parent = next((leg for leg in bot.legs if leg.position == position), None)
    if (len(bot.legs) >= MAX_LEGS or parent is None or parent.pick == "offset"
            or parent.max_offset is not None):  # fmt: skip
        return bot
    at = position + 1

    def moved(n: int | None) -> int | None:
        return n + 1 if n is not None and n >= at else n

    side = Side.BUY if parent.side is Side.SELL else Side.SELL
    child = LegDef(at, side, parent.right, parent.ratio, parent.dte, "offset",
                   Decimal(-5 if parent.right == "put" else 5), position, group=parent.group)  # fmt: skip
    legs = [replace(leg, position=moved(leg.position), link=moved(leg.link)) for leg in bot.legs]
    return replace(bot, legs=(*legs[:at], child, *legs[at:]))


def remove_leg(bot: BotDef, position: int) -> BotDef:
    """The bot without the leg at `position` (never its last leg). The legs after it
    move up one; an offset measured from it loses its link, and links to the legs
    after it follow them."""
    if len(bot.legs) <= 1 or not any(leg.position == position for leg in bot.legs):
        return bot

    def moved(n: int | None) -> int | None:
        if n is None or n == position:
            return None
        return n - 1 if n > position else n

    legs = tuple(
        replace(leg, position=i, link=moved(leg.link))
        for i, leg in enumerate(leg for leg in bot.legs if leg.position != position)
    )
    return replace(bot, legs=legs)


def add_group(bot: BotDef) -> BotDef:
    """A new empty group named "Group N" (the next number not already a name), on today's
    stop style (% above the short's fill; the controller's ruling on the final review, I3:
    "% of entry price lost" counts a credit spread's wing as worthless on its short, so as
    a default it fires early or is refused); nothing at MAX_GROUPS."""
    if len(bot.groups) >= MAX_GROUPS:
        return bot
    names = {g.name for g in bot.groups}
    n = len(bot.groups) + 1
    while f"Group {n}" in names:
        n += 1
    added = GroupDef(len(bot.groups), f"Group {n}")
    return replace(bot, groups=(*bot.groups, added))


def move_leg(bot: BotDef, position: int, step: int) -> BotDef:
    """#120 (OptionOmega's leg groups): the leg at `position` into the group `step` away
    (-1 the one above, +1 the one below); unchanged at either end. A group left empty
    stays, for another leg to move in; Save refuses an empty group."""
    leg = next((leg for leg in bot.legs if leg.position == position), None)
    if leg is None or not 0 <= leg.group + step < len(bot.groups):
        return bot
    legs = tuple(replace(x, group=x.group + step) if x is leg else x for x in bot.legs)
    return replace(bot, legs=legs)


def one_group(bot: BotDef) -> BotDef:
    """#116 (Tracker2's Use leg groups off): every leg in the first group, renamed
    "Whole trade" (its old name, like "Call side", no longer says what it holds), which
    keeps its stop and target and closes on the trade's stop alone."""
    if len(bot.groups) <= 1:
        return bot
    while len(bot.groups) > 1:
        bot = remove_group(bot, bot.groups[-1].position)
    return replace(bot, groups=(replace(bot.groups[0], name="Whole trade"),))


def remove_group(bot: BotDef, position: int) -> BotDef:
    """The bot without the group at `position` (never its last group). Its legs move
    to the first group left; the groups after it move up one."""
    if len(bot.groups) <= 1 or not any(g.position == position for g in bot.groups):
        return bot
    kept = [g for g in bot.groups if g.position != position]
    new = {g.position: i for i, g in enumerate(kept)}
    legs = tuple(replace(leg, group=new.get(leg.group, 0)) for leg in bot.legs)
    # Slice 21: a pairing follows its group up; one naming the removed group goes
    groups = tuple(
        replace(g, position=i, close_on_stop=new.get(g.close_on_stop)) for i, g in enumerate(kept)
    )
    return replace(bot, legs=legs, groups=groups)
