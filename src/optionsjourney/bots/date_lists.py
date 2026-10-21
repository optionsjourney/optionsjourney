"""Named date lists a bot can add to its specific dates or blackout days
(Tracker2's economic_events/date_sets.py): FOMC and CPI days, EOM, the last
trading day of each month, and HOLIDAY, the weekdays the market is shut (a
blackout list only, checked before expiration: decision 0083). Pure: which FOMC
and CPI days are stored, and how fresh they are, is the service's business
(services/economic_events.py); end of month and the holidays are worked out from
the market calendar (core/sessions.py)."""

import calendar
from datetime import date, timedelta

from optionsjourney.core import sessions

DATE_SETS = ("FOMC", "CPI", "EOM", "HOLIDAY")
COMPUTED_SETS = ("EOM", "HOLIDAY")
# A holiday has no session, so it's never a day to trade on (Tracker2's
# BLACKOUT_ONLY_SETS) nor an entry day (AFTER_ENTRY_ONLY_SETS): only a day a trade
# would be held over.
SPECIFIC_SETS = tuple(name for name in DATE_SETS if name != "HOLIDAY")
ENTRY_DAY_SETS = tuple(name for name in DATE_SETS if name != "HOLIDAY")
LABELS = {"FOMC": "FOMC days", "CPI": "CPI days", "EOM": "Last trading day of the month",
          "HOLIDAY": "Market holidays"}  # fmt: skip


def short_label(name: str) -> str:
    """A list's name in a sentence, "Market holiday on 2026-11-26" (date_sets.label)."""
    return "Market holiday" if name == "HOLIDAY" else name


def eom_day(year: int, month: int) -> date:
    """The month's last trading day: its last day, stepped back over weekends
    and holidays (date_sets.eom_day)."""
    day = date(year, month, calendar.monthrange(year, month)[1])
    while sessions.session_close(day) is None:
        day -= timedelta(days=1)
    return day


def eom_member(day: date) -> bool | None:
    """Whether `day` is its month's last trading day; None for a year the
    holiday calendar doesn't cover (date_sets._eom_member)."""
    if not sessions.calendar_covers(day):
        return None
    return day == eom_day(day.year, day.month)


def eom_upcoming(today: date) -> list[date]:
    """End-of-month days from `today` through the last covered year."""
    days, year, month = [], today.year, today.month
    while sessions.calendar_covers(date(year, month, 1)):
        day = eom_day(year, month)
        if day >= today:
            days.append(day)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return days


def ff_week(today: date) -> tuple[date, date]:
    """ForexFactory's week: the Sunday on or before `today`, to the Saturday after."""
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    return sunday, sunday + timedelta(days=6)


def holiday_member(day: date) -> bool | None:
    """Whether `day` is a weekday the market is shut (a half day trades); None for a
    year the holiday calendar doesn't cover (date_sets._holiday_member)."""
    if not sessions.calendar_covers(day):
        return None
    return day.weekday() < 5 and sessions.session_close(day) is None


def holiday_upcoming(today: date) -> list[date]:
    """Market holidays from `today` through the last covered year."""
    days, day = [], today
    while sessions.calendar_covers(day):
        if holiday_member(day):
            days.append(day)
        day += timedelta(days=1)
    return days
