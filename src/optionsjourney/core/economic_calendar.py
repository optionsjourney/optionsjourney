"""Reading the public calendars the bots' date lists and the holiday calendar
come from (decision 0032; Tracker2's services/economic_events/: forexfactory.py,
fomc.py, fred.py, nyse.py, keys.py and refresh.check_key_dates). Pure: the
services fetch the pages and store what these return.

- ForexFactory's weekly export: this week's USD events with times and impact.
- The Fed's FOMC calendar: day 2 (the statement day) of every meeting.
- FRED's release dates: CPI's scheduled days.
- NYSE's hours page: closures and 1:00 p.m. early closes, usually three years.

Every parser raises ValueError on a page it can't read, so a changed page
fails loudly rather than storing something wrong."""

import calendar
import contextlib
import html as htmllib
import json
import re
from collections import namedtuple
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from optionsjourney.core.market import MARKET_TZ

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FRED_URL = "https://api.stlouisfed.org/fred/release/dates"
NYSE_URL = "https://www.nyse.com/trade/hours-calendars"
CPI_RELEASE_ID = 10  # FRED's "Consumer Price Index" (keys.KEYS)
KEY_TIMES = {"FOMC": "14:00", "CPI": "08:30"}  # FRED and the Fed give dates only

# ForexFactory titles -> list, first match wins, case-insensitive (keys._TITLE_RULES).
# Only the lists bots use; every other event keeps no key.
_TITLE_RULES = (
    ("FOMC", re.compile(r"^(federal funds rate|fomc statement|fomc press conference"
                        r"|fomc economic projections)$")),
    ("CPI", re.compile(r"^(core )?cpi ")),
)  # fmt: skip
_IMPACTS = {"High": "high", "Medium": "medium"}

FFParse = namedtuple("FFParse", "rows span usd_count")


def key_for_title(title: str) -> str | None:
    lowered = (title or "").strip().lower()
    for key, pattern in _TITLE_RULES:
        if pattern.search(lowered):
            return key
    return None


def _blank(value) -> str | None:
    return (value or "").strip() or None


def _figure(value) -> str | None:
    """A forecast or previous figure ("0.3%"), None when blank."""
    text = "" if value is None else str(value).strip()
    return text[:20] or None


def parse_forexfactory(payload) -> FFParse:
    """Rows for USD High and Medium events; `span` is the first and last New York
    date over every event in the feed (the window a refresh replaces). A 00:00
    time is ForexFactory's "all day" (stored as no time); exact duplicates are
    dropped (forexfactory.parse_forexfactory)."""
    if not isinstance(payload, list):
        raise ValueError("ForexFactory's feed is not a list of events")
    rows, seen, dates, usd = [], set(), [], 0
    for event in payload:
        try:
            when = datetime.fromisoformat(event["date"]).astimezone(MARKET_TZ)
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"a ForexFactory event has no readable date: {event!r}") from e
        dates.append(when.date())
        if event.get("country") != "USD":
            continue
        usd += 1
        impact = _IMPACTS.get(event.get("impact"))
        if impact is None:
            continue
        title = (event.get("title") or "").strip()[:120]
        at = None if (when.hour, when.minute) == (0, 0) else when.strftime("%H:%M")
        if (when.date(), at, title) in seen:
            continue
        seen.add((when.date(), at, title))
        rows.append({"event_date": when.date(), "event_time": at, "title": title,
                     "impact": impact, "key": key_for_title(title), "source": "forexfactory",
                     "forecast": _figure(event.get("forecast")),
                     "previous": _figure(event.get("previous"))})  # fmt: skip
    return FFParse(rows, (min(dates), max(dates)) if dates else None, usd)


_RANK = {"high": 0, "medium": 1}


def _item(rows, label: str, key: str | None) -> dict:
    times = [r.event_time for r in rows if r.event_time]
    ordered = sorted(rows, key=lambda r: (r.event_time is not None, r.event_time or "", r.title))
    return {
        "time": min(times) if times else None,
        "label": label,
        "impact": min((r.impact for r in rows), key=lambda i: _RANK.get(i, 9)),
        "key": key,
        "prints": [
            {"title": r.title, "forecast": r.forecast, "previous": r.previous} for r in ordered
        ],
    }


def day_items(rows) -> list[dict]:
    """One day's stored events as the schedule drawer's chips (#221; Tracker2's
    query.events_for_range). A list's release (FOMC, CPI) is one item: ForexFactory's
    rows when it has them (exact time, impact, prints), else the Fed's or FRED's row
    at the list's usual time. Every other event is one item per (title, time).
    Ordered by time, all-day items first."""
    groups: dict[tuple, list] = {}
    for row in rows:
        group = ("key", row.key) if row.key else ("title", row.title, row.event_time)
        groups.setdefault(group, []).append(row)
    items = []
    for group, members in groups.items():
        if group[0] == "key":
            forexfactory = [m for m in members if m.source == "forexfactory"]
            items.append(_item(forexfactory or members, group[1], group[1]))
        else:
            items.append(_item(members, group[1], None))
    return sorted(items, key=lambda i: (i["time"] is not None, i["time"] or "",
                                        _RANK.get(i["impact"], 9), i["label"]))  # fmt: skip


_MONTHS = {name[:3].lower(): i for i, name in enumerate(calendar.month_name) if name}
_NOT_A_MEETING = ("notation vote", "unscheduled", "cancelled")
_PANEL = re.compile(r"(\d{4}) FOMC Meetings")
_CELL = re.compile(r'class="[^"]*fomc-meeting__(month|date)[^"]*"[^>]*>(.*?)</div>', re.S)


def _text(fragment: str) -> str:
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _day_two(year: int, month_text: str, date_text: str) -> date | None:
    """A meeting's second day: "27-28" -> the 28th; "Apr/May" "30-1" -> May 1;
    "Dec/Jan" "31-1" -> January 1 of the next year (fomc._day_two)."""
    months = [_MONTHS.get(part.strip()[:3].lower()) for part in month_text.split("/")]
    days = [int(n) for n in re.findall(r"\d+", date_text)]
    if not days or None in months:
        return None
    if len(months) > 1 and months[-1] < months[0]:
        year += 1
    try:
        return date(year, months[-1], days[-1])
    except ValueError:  # a garbled day; the meetings-per-year check catches it
        return None


def parse_fomc(page: str) -> dict[int, list[date]]:
    """{year: sorted statement days} for every "<YYYY> FOMC Meetings" panel.
    Notation votes, unscheduled and cancelled meetings are skipped; "*" (the
    projections marker) is ignored (fomc.parse_fomc)."""
    heads = list(_PANEL.finditer(page))
    years: dict[int, list[date]] = {}
    for k, head in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(page)
        year, found, month = int(head.group(1)), set(), None
        for cell in _CELL.finditer(page, head.end(), end):
            kind, text = cell.group(1), _text(cell.group(2))
            if kind == "month":
                month = text
                continue
            if month is None or any(word in text.lower() for word in _NOT_A_MEETING):
                month = None
                continue
            day = _day_two(year, month, text)
            if day is not None:
                found.add(day)
            month = None
        years[year] = sorted(found)
    return years


def parse_fred_dates(payload) -> list[date]:
    """FRED's release dates, sorted and unique (fred.parse_fred_dates)."""
    if not isinstance(payload, dict) or "release_dates" not in payload:
        message = payload.get("error_message") if isinstance(payload, dict) else None
        raise ValueError(f"FRED returned no release dates: {message or 'unexpected answer'}")
    return sorted({date.fromisoformat(r["date"]) for r in payload["release_dates"]})


_WEEKDAYS = list(calendar.day_name)
_MONTH_NAMES = {name: i for i, name in enumerate(calendar.month_name) if name}
_DAY = re.compile(
    rf"({'|'.join(_WEEKDAYS)}),\s+({'|'.join(_MONTH_NAMES)})\s+(\d{{1,2}})(?:,\s+(\d{{4}}))?"
)
# An early-close footnote, to the end of its text: a JSON string or an HTML paragraph
# (the page has carried both, #222)
_EARLY = re.compile(r'close early at (\d{1,2}):(\d{2}) p\.m\.([^<"]*)')


@dataclass
class NyseCalendar:
    closed: dict[date, str] = field(default_factory=dict)  # day -> holiday
    early: dict[date, time] = field(default_factory=dict)  # day -> close, New York
    years: list[int] = field(default_factory=list)


def _named_day(match, year: int | None = None) -> date:
    """A "Friday, July 3" date; its weekday must match, so a shifted column or a
    misread year fails loudly (nyse._date)."""
    weekday, month, day, stated = match.groups()
    value = date(int(stated or year), _MONTH_NAMES[month], int(day))
    if _WEEKDAYS[value.weekday()] != weekday:
        raise ValueError(
            f"NYSE says {match.group(0)!r}, but that is a {_WEEKDAYS[value.weekday()]}"
        )
    return value


def _components(page: str, name: str):
    """Each CMS component called `name` that the page carries as inline JSON."""
    decoder, marker = json.JSONDecoder(), f'"name":"{name}"'
    at = page.find(marker)
    while at != -1:
        start = page.rfind('{"id":', 0, at)
        with contextlib.suppress(ValueError):  # not a whole component: look on
            yield decoder.raw_decode(page, start)[0]
        at = page.find(marker, at + len(marker))


def parse_nyse(page: str) -> NyseCalendar:
    """Closures from the table whose header row starts "Holiday" and names one
    year a column; early closes from the footnotes, whose dates carry their
    year (nyse.parse_nyse)."""
    result = NyseCalendar()
    for table in _components(page, "cms-component-table"):
        rows = [
            [cell.get("text", "").strip() for cell in row.get("cells", [])]
            for section in table.get("props", {}).get("sections", [])
            for row in section.get("rows", [])
        ]
        if not rows or not rows[0] or rows[0][0] != "Holiday":
            continue
        result.years = [int(y) for y in rows[0][1:]]
        for row in rows[1:]:
            for year, text in zip(result.years, row[1:], strict=False):
                found = _DAY.search(text)
                if found:
                    result.closed[_named_day(found, year)] = row[0][:80]
        break
    else:
        raise ValueError("NYSE's page has no holiday table (a header starting 'Holiday')")
    for early in _EARLY.finditer(page):
        close = time(int(early.group(1)) + 12, int(early.group(2)))
        for found in _DAY.finditer(early.group(3)):
            if found.group(4):  # footnote dates carry their year
                result.early[_named_day(found)] = close
    return result


def check_dates(key: str, new: list[date], stored: set[date], today: date) -> str | None:
    """Why `new` mustn't replace `stored`, or None (refresh.check_key_dates): a
    weekend date, or a date stored before for today or later that's missing now
    (a half-broken parser dropping a meeting or a holiday). An empty upcoming
    list on its own is fine (December, before next year's schedule is out)."""
    weekend = [d.isoformat() for d in new if d.weekday() >= 5]
    if weekend:
        return f"{key}: weekend dates {', '.join(weekend)}"
    fresh = set(new)
    missing = sorted(d.isoformat() for d in stored if d >= today and d not in fresh)
    if missing:
        return f"{key}: dates stored before are missing now: {', '.join(missing)}"
    return None


NYSE_MIN_CLOSURES = 9  # NYSE lists 9-10 a year (no New Year's when it's a Saturday)
SOON = 7  # days ahead in which a new closure is never taken on the page's word


def nyse_problem(
    parsed: NyseCalendar,
    today: date,
    table_closed: frozenset[date],
    table_early: frozenset[date],
    is_open: Callable[[date], bool],
) -> str | None:
    """Why a parsed NYSE page mustn't replace the calendar, or None. For a year the
    built-in table covers, its closures and 1:00 p.m. early closes must equal the
    table's (a reworded footnote or an unreadable cell would otherwise change a
    known day). A later year needs at least 9 closures and an early close. A
    closure in the next week that the calendar in use doesn't have is refused:
    a wrongly read day would stop entries and exits being worked that day."""
    outside = sorted({d.year for d in [*parsed.closed, *parsed.early]} - set(parsed.years))
    if outside:
        # A cell or footnote naming another year would otherwise mark that whole year
        # as covered, with none of its other holidays.
        years = ", ".join(str(y) for y in outside)
        return f"dates in {years}, a year the page's table doesn't list"
    table_years = {d.year for d in table_closed}
    for year in parsed.years:
        closed = {d for d in parsed.closed if d.year == year}
        early = {d: t for d, t in parsed.early.items() if d.year == year}
        if year in table_years:
            if closed != {d for d in table_closed if d.year == year}:
                return f"closures for {year} don't match the built-in table"
            if early != {d: time(13, 0) for d in table_early if d.year == year}:
                return f"early closes for {year} don't match the built-in table"
        elif len(closed) < NYSE_MIN_CLOSURES:
            return f"fewer than {NYSE_MIN_CLOSURES} closures for {year}"
        elif not early:
            return f"no early close for {year}"
    soon = sorted(
        d for d in parsed.closed if today <= d <= today + timedelta(days=SOON) and is_open(d)
    )
    if soon:
        days = ", ".join(d.isoformat() for d in soon)
        return f"new closures in the next {SOON} days the calendar doesn't have: {days}"
    return None
