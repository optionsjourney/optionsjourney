"""What each armed bot does on a day (bots slice 20; Tracker2's automation_bp.api_schedule,
_run_outcome and floating.last_check): one row per enabled entry time, or one for a
floating window, saying whether it will actually trade, not merely that it's scheduled.
Pure: the caller passes the bots, the day's runs, the clock and what it knows of the day.

Another day is a forecast: the day checks (the schedule, blackout days, the holiday and
early-close calendar) apply, but Freeze and No new trades block only today (they may be
off by then, so another day only notes them), and the market filters are decided when
each entry fires."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time

from optionsjourney.bots import floating, schedule
from optionsjourney.bots.definition import BotDef

STATES = ("unscheduled", "pending", "watching", "running", "done", "unfilled", "skipped",
          "error", "missed", "blocked")  # fmt: skip
LABELS = {"unscheduled": "not scheduled"}
PAST = time(23, 59, 59)
NO_RUN = "no run recorded"


@dataclass(frozen=True)
class Row:
    time: str  # "HH:MM", or "HH:MM–HH:MM" for a floating window
    bot_id: int
    bot_name: str
    live: bool
    size: str
    state: str
    note: str | None = None
    run_id: int | None = None
    floating: bool = False
    can_skip: bool = False
    underlying: str = ""
    broker: str = ""
    account_number: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DayPlan:
    day: date
    is_today: bool
    rows: list[Row] = field(default_factory=list)  # blocked rows are counted, not listed
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # the notes that need a look
    market_closed: bool = False
    clock_ok: bool | None = None  # the schedule check: None when it can't be told


def outcome(run) -> tuple[str, str]:
    """(state, note) for an entry time that has a run (_run_outcome). A run only means the
    clock fired: a filter refusal or a failure is recorded too, and must not read as a
    trade. A run "missed" is the chase giving up, so "unfilled" here: the schedule's own
    "missed" means the time never fired at all."""
    status, reason = run.status, (run.reason or "").strip()
    if status in ("filled", "partial", "preview"):
        return "done", status
    if status in ("pending", "running"):
        return "running", "working"
    if status == "missed":
        return "unfilled", "not filled"
    if status == "skipped":
        return "skipped", f"skipped · {reason}" if reason else "skipped"
    return "error", f"error · {reason}" if reason else "error"


def _who(bot: BotDef) -> dict:
    """What a row shows under the name: the underlying, the account and the bot's tags."""
    return dict(underlying=bot.underlying, broker=bot.broker, account_number=bot.account_number,
                tags=tuple(bot.tags))  # fmt: skip


def _size(bot: BotDef, quantity: int | None = None) -> str:
    if bot.sizing == "dollars" and bot.max_per_entry_usd is not None:
        return f"${bot.max_per_entry_usd:.0f} of buying power an entry"
    n = quantity or bot.contracts
    return f"{n} contract{'' if n == 1 else 's'}"


def plan(
    bots: list[BotDef],
    runs: list,
    day: date,
    now_et: datetime,
    close: time | None,
    lists: Mapping[str, bool | None] | None = None,
    ahead: schedule.Ahead | None = None,
    freeze: bool = False,
    no_new_trades: bool = False,
    clock_ok: bool | None = None,
    floating_last: Callable[[int], tuple[str, str] | None] | None = None,
) -> DayPlan:
    """The day's rows for the armed `bots` (`runs`: every run of that day; `now_et`: New
    York, naive or not; `close`: the session's close that day, None when the market is
    shut; `clock_ok`: False when the schedule check says the clock has stopped; `lists`:
    the day's answer for each list, `ahead`: each Before expiration list's answers for the
    days after it, through the longest expiry estimate, issue #207)."""
    lists, ahead = lists or {}, ahead or {}
    is_today = day == now_et.date()
    current = now_et.time() if is_today else (PAST if day < now_et.date() else None)
    # The cut-off from the close passed in (schedule.entry_cutoff reads the same calendar)
    cutoff = None if close is None else (datetime.combine(day, close) - schedule.CUTOFF).time()
    blocked_today = is_today and (freeze or no_new_trades)
    roots = [r for r in runs if getattr(r, "parent_run_id", None) is None]
    rows: list[Row] = []
    blocked = blocked_live = 0  # the drawer's Live and Paper tabs count them apart
    cant_tell: set[str] = set()

    def past(hhmm: str) -> bool:
        """Past the clock's grace (schedule.GRACE): until then the clock may still fire it."""
        if current is None:
            return False
        due = datetime.combine(day, time.fromisoformat(hhmm)) + schedule.GRACE
        return datetime.combine(day, current) > due

    def day_block(bot: BotDef) -> str | None:
        if blocked_today:
            return "Freeze is on" if freeze else "No new trades is on"
        if close is None:
            return "market closed"
        unknown = schedule.unavailable_blackout_lists(bot, day, lists, ahead)
        if unknown:
            cant_tell.update(unknown)
            return f"the {', '.join(unknown)} list can't tell"
        if schedule.is_blackout(bot, day, lists, ahead):
            hit = schedule.blackout_lists_hit(bot, day, lists, ahead)
            return "blackout day" + (f" ({', '.join(hit)})" if hit else "")
        return None

    def blocks(bot: BotDef, hhmm: str) -> bool:
        """Not on this day (Freeze today, the market shut, a blackout), or past the cut-off."""
        late = cutoff is not None and time.fromisoformat(hhmm) >= cutoff
        return day_block(bot) is not None or late

    for bot in bots:
        own = [r for r in roots if r.bot_id == bot.id]
        on_schedule = schedule.runs_on(bot, day, lists)
        window = floating.window(bot)
        if window:
            start, end = window
            prefix = floating.prefix(bot.id, day)
            mine = sorted((r for r in own if r.key.startswith(prefix)), key=lambda r: r.id)
            tries = [r for r in mine if not r.key.endswith(floating.CLOSE_SUFFIX)]
            ended = next((r for r in mine if r.key.endswith(floating.CLOSE_SUFFIX)), None)
            run = tries[-1] if tries else ended
            if run is None and not on_schedule:
                continue
            open_now = is_today and start <= now_et.strftime("%H:%M") <= end
            if run is not None:
                state, note = outcome(run)
                if state == "unfilled" and open_now:
                    note = "not filled · retrying"
            elif blocks(bot, start):
                blocked += 1
                blocked_live += bot.live
                continue
            elif current is not None and current.strftime("%H:%M") > end:
                state, note = "missed", NO_RUN
            elif open_now:
                last = floating_last(bot.id) if floating_last else None
                state = "watching"
                note = f"last check {last[0]} · {last[1]}" if last else "checking each minute"
            else:
                state, note = "pending", None
            rows.append(Row(floating.label(bot), bot.id, bot.name, bot.live, _size(bot), state,
                            note, run.id if run else None, floating=True, **_who(bot)))  # fmt: skip
            continue
        for entry in bot.entry_times:
            if not entry.enabled:
                continue
            key = schedule.entry_key(bot.id, entry.time, day)
            run = next((r for r in own if r.key == key), None)
            if run is None and not on_schedule:
                continue  # not on its schedule that day (Tracker2 leaves it out)
            if run is not None:
                state, note = outcome(run)
            elif blocks(bot, entry.time):
                blocked += 1
                blocked_live += bot.live
                continue
            elif past(entry.time):
                state, note = "missed", NO_RUN
            else:
                state, note = "pending", None
            rows.append(Row(entry.time, bot.id, bot.name, bot.live, _size(bot, entry.quantity),
                            state, note, run.id if run else None, **_who(bot)))  # fmt: skip

    # Today or a later day can skip (Tracker2's can_skip): the day goes into blackout days
    still_to_come = day >= now_et.date()
    final = []
    for row in rows:
        if clock_ok is False and is_today and row.state in ("pending", "watching"):
            row = replace(row, state="unscheduled", note="the clock has stopped: won't fire")
        elif still_to_come and row.state in ("pending", "watching"):
            row = replace(row, can_skip=True)
        final.append(row)
    final.sort(key=lambda r: (r.time, r.bot_name.lower()))
    counts = {state: sum(1 for r in final if r.state == state) for state in STATES}
    counts["blocked"] = blocked
    counts["blocked_live"] = blocked_live

    notes, warnings = [], []
    if blocked_today:
        what = "Freeze" if freeze else "No new trades"
        warnings.append(f"{what} is on: nothing new opens today. Exits on open trades keep "
                        "running.")  # fmt: skip
        notes.append(warnings[-1])
    elif not is_today and day > now_et.date() and (freeze or no_new_trades):
        what = "Freeze" if freeze else "No new trades"
        warnings.append(f"{what} is on right now: these won't open unless it's off by then.")
        notes.append(warnings[-1])
    if clock_ok is False and is_today:
        warnings.append("The bot clock has stopped: rows marked not scheduled won't fire. "
                        "Check the app's log, then restart it.")  # fmt: skip
        notes.append(warnings[-1])
    if close is not None and close != time(16, 0):
        notes.append(f"Early close at {close:%H:%M}: entries stop at "
                     f"{cutoff:%H:%M} ET.")  # fmt: skip
    if cant_tell:
        warnings.append(f"The {', '.join(sorted(cant_tell))} list can't tell whether this day "
                        "is in it: a blackout list that can't tell skips the entry.")  # fmt: skip
        notes.append(warnings[-1])
    if not is_today and day > now_et.date() and final:
        notes.append("Scheduled, not guaranteed: the market filters are checked when each "
                     "entry fires.")  # fmt: skip
    return DayPlan(day, is_today, final, counts, notes, warnings, market_closed=close is None,
                   clock_ok=clock_ok)  # fmt: skip
