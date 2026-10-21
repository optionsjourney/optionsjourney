"""When a bot's entry times fire (spec: the first automation, section 2;
Tracker2's registry.py, guards.py). Pure: the clock passes the time in."""

import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta

from optionsjourney.bots.date_lists import DATE_SETS, ENTRY_DAY_SETS, short_label
from optionsjourney.bots.definition import BotDef, EntryTimeDef
from optionsjourney.core.sessions import session_close

GRACE = timedelta(minutes=3)  # later than this, an entry is skipped, never fired late
CUTOFF = timedelta(minutes=2)  # no entries in the last 2 minutes of the session


def entry_key(bot_id: int, hhmm: str, day: date) -> str:
    return f"bot-{bot_id}-{hhmm.replace(':', '')}-{day:%Y%m%d}"


def manual_key(bot_id: int, now: datetime) -> str:
    return f"bot-{bot_id}-now-{now:%Y%m%d%H%M%S%f}"


# Runs you start yourself (decision 0058): the Trade page's (services/bot_ticket.py),
# a taken-over trade's (services/bot_adopt.py) and Trade now's (trade_key).
BY_HAND_PREFIXES = ("ticket-", "adopt-")
TRADE_NOW = "-trade-"


def by_hand(key: str | None) -> bool:
    """Whether a run's key is one you started yourself, not the bot by its schedule."""
    return bool(key) and (key.startswith(BY_HAND_PREFIXES) or TRADE_NOW in key)


def trade_key(bot_id: int, now: datetime) -> str:
    """A Trade now run's key (bots slice 19; orchestrator._idempotency_key's manual shape,
    with a time in it, so each press is its own run). Unlike Preview now's it counts as
    one of the day's entries."""
    return f"bot-{bot_id}-trade-{now:%Y%m%d%H%M%S%f}"


SECOND_STEP = 15  # an entry time's seconds go in 15-second steps (Tracker2)


def normalize_time(text: str | None, seconds: bool = False) -> str | None:
    """A typed time as stored, else None (Tracker2's normalize_time): 945, 0945 and 9:45
    are 09:45. Three digits are padded on the left, never guessed at: 130 is 01:30. With
    seconds (entry times only), 9:30:15 and 93015 are 09:30:15 and zero seconds are
    dropped, so 09:30 and 09:30:00 can't both be on a bot. Hours and minutes are checked
    here; the range and the 15-second step are validation's."""
    raw = (text or "").strip()
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) not in ((2, 3) if seconds else (2,)):
            return None
    elif raw.isdigit() and (len(raw) in (3, 4) or (seconds and len(raw) in (5, 6))):
        padded = raw.zfill(4 if len(raw) <= 4 else 6)
        parts = [padded[i : i + 2] for i in range(0, len(padded), 2)]
    else:
        return None
    if not (1 <= len(parts[0]) <= 2 and all(len(p) == 2 for p in parts[1:])):
        return None
    if not all(p.isdigit() for p in parts):
        return None
    hour, minute, *rest = (int(p) for p in parts)
    if hour > 23 or minute > 59 or (rest and rest[0] > 59):
        return None
    shown = f"{hour:02d}:{minute:02d}"
    return f"{shown}:{rest[0]:02d}" if rest and rest[0] else shown


def clock(text: str) -> time:
    """A stored HH:MM or HH:MM:SS as a time."""
    return time(*(int(part) for part in text.split(":")))


def entry_cutoff(day: date) -> time | None:
    close = session_close(day)
    if close is None:
        return None
    return (datetime.combine(day, close) - CUTOFF).time()


DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_date(text: str) -> bool:
    """A real calendar date written YYYY-MM-DD."""
    if not DATE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def parse_date_list(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(good, bad) from a pasted list of dates split on spaces, commas and new
    lines. Good dates are de-duplicated and sorted; bad tokens keep their order."""
    good, bad = set(), []
    for token in re.split(r"[\s,]+", text or ""):
        if not token:
            continue
        if is_date(token):
            good.add(token)
        elif token not in bad:
            bad.append(token)
    return tuple(sorted(good)), tuple(bad)


def is_nth_weekday(day: date, week: int, weekday: int) -> bool:
    """Is `day` the `week`-th (1-5) `weekday` (1 = Monday) of its month?"""
    return day.isoweekday() == weekday and (day.day - 1) // 7 + 1 == week


# Whether a day is in each named list (FOMC, CPI, EOM, HOLIDAY): True, False, or None
# when the list can't tell (services/economic_events.lists_for works it out).
Lists = Mapping[str, bool | None]
# The same for the days after an entry day, list by list (economic_events.lists_ahead):
# a day left out counts as one the list can't tell.
Ahead = Mapping[str, Mapping[date, bool | None]]


def runs_on(bot: BotDef, day: date, lists: Lists | None = None) -> bool:
    """Does the bot's frequency allow an entry on `day`? Specific dates are the
    typed ones plus the ticked lists' days; a list that can't tell adds no day
    (guards._frequency_check)."""
    if bot.frequency == "weekly":
        return day.isoweekday() in bot.entry_days
    if bot.frequency == "monthly":
        if bot.monthly_week is None or bot.monthly_weekday is None:
            return False
        return is_nth_weekday(day, bot.monthly_week, bot.monthly_weekday)
    if bot.frequency == "specific_dates":
        lists = lists or {}
        return day.isoformat() in bot.specific_dates or any(
            lists.get(name) is True for name in bot.specific_date_sets
        )
    return bot.frequency == "daily"  # an unknown value never runs


_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEK_NAMES = ("1st", "2nd", "3rd", "4th", "5th")


def _day_runs(days: list[int]) -> str:
    """Weekdays in a few words: three or more in a row read as a range, "Mon–Fri"."""
    runs: list[list[int]] = []
    for d in days:
        if runs and d == runs[-1][-1] + 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return ", ".join(
        f"{_DAY_NAMES[r[0] - 1]}–{_DAY_NAMES[r[-1] - 1]}" if len(r) >= 3
        else ", ".join(_DAY_NAMES[d - 1] for d in r)
        for r in runs
    )  # fmt: skip


def summary(bot: BotDef) -> tuple[str, str]:
    """The bots list's Schedule column: (which days, which times), in a few words."""
    if bot.frequency == "weekly":
        days = _day_runs(sorted({d for d in bot.entry_days if 1 <= d <= 7})) or "No days ticked"
    elif bot.frequency == "monthly":
        week, weekday = bot.monthly_week, bot.monthly_weekday
        ok = week in range(1, 6) and weekday in range(1, 8)
        days = f"{_WEEK_NAMES[week - 1]} {_DAY_NAMES[weekday - 1]} monthly" if ok else "Monthly"
    elif bot.frequency == "specific_dates":
        parts = list(bot.specific_date_sets)
        if bot.specific_dates:
            n = len(bot.specific_dates)
            parts.append(f"{n} date{'s' if n != 1 else ''}")
        days = ", ".join(parts) or "No dates"
    else:
        days = "Every day"
    if bot.use_floating_entry:
        times = f"{bot.floating_start or '?'}–{bot.floating_end or '?'}"
    else:
        on = [t.time[:5] for t in bot.entry_times if t.enabled]
        times = ", ".join(on) or "No times"
    return days, times


def _in_order(names) -> tuple[str, ...]:
    """Known list names, once each, in DATE_SETS order (guards._set_names)."""
    ticked = set(names)
    return tuple(name for name in DATE_SETS if name in ticked)


def blackout_entry_lists(bot: BotDef) -> tuple[str, ...]:
    """The lists checked on the entry day itself (Entry day), while blackout days is on.
    A market holiday never is one: it has no session (guards.blackout_entry_names)."""
    if not bot.use_blackout_days:
        return ()
    return tuple(n for n in _in_order(bot.blackout_date_sets) if n in ENTRY_DAY_SETS)


def blackout_expiry_lists(bot: BotDef) -> tuple[str, ...]:
    """The lists checked on each day after the entry day through the trade's expiration
    (Before expiration), while blackout days is on; a market holiday saved as an entry-day
    list counts as one of these (guards.blackout_dte_names)."""
    if not bot.use_blackout_days:
        return ()
    held_over = [n for n in bot.blackout_date_sets if n not in ENTRY_DAY_SETS]
    return _in_order([*bot.blackout_dte_sets, *held_over])


def expiry_estimate(bot: BotDef, day: date) -> date:
    """When a trade entered on `day` would expire: `day` plus the longest leg's DTE in
    calendar days, rolled on to the next session (with exact DTE off, the first listing
    at or after the DTE is picked). guards.blackout_expiry_estimate."""
    longest = max((leg.dte for leg in bot.legs), default=0)
    if longest <= 0:
        return day
    expiry = day + timedelta(days=longest)
    for _ in range(14):
        if session_close(expiry) is not None:
            break
        expiry += timedelta(days=1)
    return expiry


def expiry_window(bot: BotDef, day: date) -> tuple[date, ...]:
    """The days after `day` up to and including the expiry estimate: none for 0DTE."""
    last = expiry_estimate(bot, day)
    return tuple(day + timedelta(days=n) for n in range(1, (last - day).days + 1))


def blackout_lists_hit(
    bot: BotDef, day: date, lists: Lists | None = None, ahead: Ahead | None = None
) -> list[str]:
    """Why an entry on `day` is blacked out by a list (only while blackout days is on):
    "FOMC" for an Entry day list that has `day`; "FOMC on 2026-10-28, before expiration"
    for a Before expiration list with a day after `day` through the expiry estimate, so
    an entry on the event day itself can still trade. A list may be both
    (guards.blackout_sets_hit). A list that can't tell never hits here."""
    lists, ahead = lists or {}, ahead or {}
    hits = [name for name in blackout_entry_lists(bot) if lists.get(name) is True]
    names = blackout_expiry_lists(bot)
    window = expiry_window(bot, day) if names else ()
    for name in names:
        found = next((d for d in window if ahead.get(name, {}).get(d) is True), None)
        if found is not None:
            hits.append(f"{short_label(name)} on {found.isoformat()}, before expiration")
    return hits


def is_blackout(
    bot: BotDef, day: date, lists: Lists | None = None, ahead: Ahead | None = None
) -> bool:
    return bot.use_blackout_days and (
        day.isoformat() in bot.blackout_days or bool(blackout_lists_hit(bot, day, lists, ahead))
    )


def unavailable_blackout_lists(
    bot: BotDef, day: date, lists: Lists, ahead: Ahead | None = None
) -> list[str]:
    """Blackout lists that can't say whether an entry on `day` is blacked out: one that
    can't tell about `day` itself, or a Before expiration list that can't tell about a
    day after it through the expiry estimate. The entry is skipped for these: a blackout
    fails closed (the spec's question 1; Tracker2 looks at the entry day only)."""
    ahead = ahead or {}
    expiry_names = blackout_expiry_lists(bot)
    window = expiry_window(bot, day) if expiry_names else ()
    missing = {n for n in (*blackout_entry_lists(bot), *expiry_names) if lists.get(n) is None}
    missing |= {
        name for name in expiry_names if any(ahead.get(name, {}).get(d) is None for d in window)
    }
    return list(_in_order(missing))


def lists_in_use(bot: BotDef) -> tuple[str, ...]:
    """The lists the bot relies on today: its specific-dates lists on that
    frequency, its blackout lists (both checks) while blackout days is on
    (guards.unavailable_sets)."""
    names = set()
    if bot.frequency == "specific_dates":
        names |= set(bot.specific_date_sets)
    if bot.use_blackout_days:
        names |= set(bot.blackout_date_sets) | set(bot.blackout_dte_sets)
    return tuple(sorted(names))


def skip_target_day(now_et: datetime) -> date:
    """The day a "skip the next entry" applies to: today until the session
    closes, then the next day the market is open."""
    today = now_et.date()
    close = session_close(today)
    if close is not None and now_et.time() < close:
        return today
    day = today + timedelta(days=1)
    while session_close(day) is None:
        day += timedelta(days=1)
    return day


def due(
    entry_times: tuple[EntryTimeDef, ...], now_et: datetime, done: set[str], bot_id: int
) -> list[tuple[str, str]]:
    """Entry times that are due now (New York, naive): fire within GRACE of
    the time, skip once past it. Times already run (their key in `done`) and
    times still to come are left out."""
    found = []
    for entry in entry_times:
        hhmm = entry.time
        if not entry.enabled:
            continue
        at = datetime.combine(now_et.date(), clock(hhmm))
        if now_et < at or entry_key(bot_id, hhmm, now_et.date()) in done:
            continue
        found.append((hhmm, "fire" if now_et - at <= GRACE else "skip"))
    return found
