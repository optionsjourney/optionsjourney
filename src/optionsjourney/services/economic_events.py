"""Economic events and the market calendar (decision 0032; Tracker2's
services/economic_events/refresh.py and date_sets.py).

- **Refreshing:** fetch, parse (core/economic_calendar.py), check, then store,
  per source. A source whose fetch or check fails keeps its old rows, records
  the error and alerts (at most once a day per source).
- **When:** `due_sources` / `refresh_if_due`, from a background loop; "Refresh
  now" runs `refresh_all`.
- **Lists:** `is_member` answers whether a day is in FOMC, CPI or EOM: True,
  False, or None when it can't tell.
- **The calendar:** `load_calendar` hands the stored NYSE days to core/sessions.
- **The schedule drawer:** `day_notes` gives a day's events and its holiday."""

import logging
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import httpx
from sqlalchemy import delete, select

from optionsjourney.bots import date_lists
from optionsjourney.core import economic_calendar as ec
from optionsjourney.core import sessions
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import EconomicEvent, EconomicEventSource, MarketCalendarDay
from optionsjourney.services import settings
from optionsjourney.services.user_alerts import Fired

logger = logging.getLogger(__name__)

FOREXFACTORY, FED, FRED_CPI, NYSE = "forexfactory", "fed", "fred:CPI", "nyse"
SOURCES = (FOREXFACTORY, FED, FRED_CPI, NYSE)
LABELS = {FOREXFACTORY: "ForexFactory", FED: "FOMC", FRED_CPI: "FRED CPI", NYSE: "NYSE calendar"}
# A list's backing rows: (EconomicEvent.source, its status row) (date_sets._BACKING)
BACKING = {"FOMC": ("fed", FED), "CPI": ("fred", FRED_CPI)}
AVAILABLE_FOR = timedelta(days=21)  # date_sets.AVAILABLE_DAYS
REFRESH_EVERY = timedelta(days=7)
RETRY_AFTER = timedelta(hours=1)
FF_NEW_WEEK_AT = time(18, 0)  # Sunday evening, when Tracker2's weekly sync runs
MIN_FOMC_PER_YEAR = 6
TIMEOUT_S = 20
USER_AGENT = "Mozilla/5.0 (OptionsJourney)"  # ForexFactory's export wants a browser's


def _get(url: str, params: dict | None = None) -> httpx.Response:
    response = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT_S, follow_redirects=True)  # fmt: skip
    response.raise_for_status()
    return response


def fetch_forexfactory():
    return _get(ec.FF_URL).json()


def fetch_fomc() -> str:
    return _get(ec.FOMC_URL).text


def fetch_fred(release_id: int, api_key: str, start: date):
    """FRED's release dates from `start`. It puts its reason in the JSON even on a
    400, so that's returned for the parser to report."""
    params = {"release_id": release_id, "api_key": api_key, "file_type": "json",
              "include_release_dates_with_no_data": "true", "realtime_start": start.isoformat(),
              "realtime_end": "9999-12-31", "sort_order": "asc", "limit": 10000}  # fmt: skip
    response = httpx.get(ec.FRED_URL, params=params, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT_S)  # fmt: skip
    try:
        return response.json()
    except ValueError:
        response.raise_for_status()
        raise


def fetch_nyse() -> str:
    return _get(ec.NYSE_URL).text


@dataclass(frozen=True)
class Fetchers:
    """Where each source comes from; tests pass fakes."""

    forexfactory: Callable[[], object] = fetch_forexfactory
    fomc: Callable[[], str] = fetch_fomc
    fred: Callable[[int, str, date], object] = fetch_fred
    nyse: Callable[[], str] = fetch_nyse


class _RedactKeys(logging.Filter):
    """Backstop: an api_key=... query value never reaches the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = _scrub(message)
        if clean != message:
            record.msg, record.args = clean, None
        return True


def quiet_http_logs() -> None:
    """httpx logs every request URL at INFO, FRED's key included: keep httpx and
    httpcore at WARNING, and redact api_key values if they're turned up again.
    Called by both entry points after logging is set up."""
    for name in ("httpx", "httpcore"):
        found = logging.getLogger(name)
        found.setLevel(logging.WARNING)
        if not any(isinstance(f, _RedactKeys) for f in found.filters):
            found.addFilter(_RedactKeys())


def _today(now: datetime) -> date:
    return now.astimezone(MARKET_TZ).date()


def _scrub(message: str, key: str | None = None) -> str:
    """No FRED key in a stored error or an alert (refresh._scrub)."""
    message = re.sub(r"api_key=[^&\s]+", "api_key=***", message)
    return message.replace(key, "***") if key else message


_alerted: set[tuple[str, date]] = set()
_alerted_lock = threading.Lock()


def reset_alerts() -> None:
    with _alerted_lock:
        _alerted.clear()


def _alert(session_factory, notify, source: str, message: str, now: datetime) -> None:
    """One alert per source per New York day while it keeps failing."""
    with _alerted_lock:
        if (source, _today(now)) in _alerted:
            return
        _alerted.add((source, _today(now)))
    event = Fired(
        0,
        f"Market calendar: {LABELS[source]} refresh failed",
        f"{message}. Kept the dates it had; it tries again within the hour.",
        "/automation",
    )
    try:
        if notify is not None:
            notify(event)
        else:
            from optionsjourney.services import notify as sender  # noqa: PLC0415

            sender.send(session_factory, event)
    except Exception:  # noqa: BLE001 -- an alert never stops a refresh
        logger.exception("couldn't send a market calendar alert")


def _status(session, source: str) -> EconomicEventSource:
    row = session.get(EconomicEventSource, source)
    if row is None:
        row = EconomicEventSource(source=source)
        session.add(row)
    return row


def _fail(session_factory, notify, source, error, now, key=None) -> str:
    message = _scrub(str(error) or error.__class__.__name__, key)
    try:
        with session_factory() as session:
            row = _status(session, source)
            row.last_attempt_at, row.last_error = now, message
            session.commit()
    except Exception:  # noqa: BLE001 -- still log and alert the refresh's own failure
        logger.exception("couldn't record the %s refresh failure", source)
    logger.warning("market calendar: %s refresh failed: %s", source, message)
    _alert(session_factory, notify, source, message, now)
    return message


def _ok(session, source: str, now: datetime) -> None:
    row = _status(session, source)
    row.last_attempt_at = row.refreshed_at = now
    row.last_error = None


def _stored(session, source: str, key: str) -> set[date]:
    return set(
        session.scalars(
            select(EconomicEvent.event_date).where(
                EconomicEvent.source == source, EconomicEvent.key == key
            )
        )
    )


def _replace_key(session, source: str, key: str, days: Iterable[date], since: date) -> None:
    """A list's dates from `since` on; earlier ones are kept (refresh._replace_key)."""
    session.execute(
        delete(EconomicEvent).where(
            EconomicEvent.source == source,
            EconomicEvent.key == key,
            EconomicEvent.event_date >= since,
        )
    )
    session.add_all(
        EconomicEvent(event_date=d, event_time=ec.KEY_TIMES[key], title=key, impact="high",
                      key=key, source=source)
        for d in days
    )  # fmt: skip


def refresh_forexfactory(session_factory, now, fetch, notify=None) -> str | None:
    """This week's events; the feed must cover today (refresh_forexfactory)."""
    today = _today(now)
    try:
        parsed = ec.parse_forexfactory(fetch())
        if parsed.usd_count == 0:
            raise ValueError("no USD events in the feed")
        start, end = parsed.span
        if not start <= today <= end:
            raise ValueError(f"the feed is for {start}..{end}, not the current week")
        with session_factory() as session:
            session.execute(
                delete(EconomicEvent).where(
                    EconomicEvent.source == FOREXFACTORY,
                    EconomicEvent.event_date >= start,
                    EconomicEvent.event_date <= end,
                )
            )
            session.add_all(EconomicEvent(**row) for row in parsed.rows)
            _ok(session, FOREXFACTORY, now)
            session.commit()
        return None
    except Exception as e:  # noqa: BLE001 -- any failure keeps the old rows
        return _fail(session_factory, notify, FOREXFACTORY, e, now)


def refresh_fomc(session_factory, now, fetch, notify=None) -> str | None:
    """The Fed's meetings from today on (refresh_fomc)."""
    today = _today(now)
    try:
        years = ec.parse_fomc(fetch())
        if not years:
            raise ValueError("no '<year> FOMC Meetings' panels on the page")
        thin = [str(y) for y, days in sorted(years.items()) if len(days) < MIN_FOMC_PER_YEAR]
        if thin:
            raise ValueError(f"fewer than {MIN_FOMC_PER_YEAR} meetings read for {', '.join(thin)}")
        upcoming = sorted({d for days in years.values() for d in days if d >= today})
        with session_factory() as session:
            problem = ec.check_dates("FOMC", upcoming, _stored(session, "fed", "FOMC"), today)
            if problem:
                raise ValueError(problem)
            _replace_key(session, "fed", "FOMC", upcoming, today)
            _ok(session, FED, now)
            session.commit()
        return None
    except Exception as e:  # noqa: BLE001
        return _fail(session_factory, notify, FED, e, now)


def refresh_fred(session_factory, now, fetch, api_key: str, notify=None) -> str | None:
    """CPI's scheduled days from today on (refresh_fred), with the user's key."""
    today = _today(now)
    try:
        days = [d for d in ec.parse_fred_dates(fetch(ec.CPI_RELEASE_ID, api_key, today))
                if d >= today]  # fmt: skip
        with session_factory() as session:
            problem = ec.check_dates("CPI", days, _stored(session, "fred", "CPI"), today)
            if problem:
                raise ValueError(problem)
            _replace_key(session, "fred", "CPI", days, today)
            _ok(session, FRED_CPI, now)
            session.commit()
        return None
    except Exception as e:  # noqa: BLE001
        return _fail(session_factory, notify, FRED_CPI, e, now, api_key)


def refresh_nyse(session_factory, now, fetch, notify=None) -> str | None:
    """Every year NYSE's page lists, in full; then the market uses it (refresh_nyse)."""
    today = _today(now)
    try:
        parsed = ec.parse_nyse(fetch())
        if today.year not in parsed.years:
            raise ValueError(f"the page lists {parsed.years}, not {today.year}")
        problem = ec.nyse_problem(
            parsed,
            today,
            sessions.NYSE_HOLIDAYS,
            sessions.NYSE_EARLY_CLOSES,
            lambda day: sessions.session_close(day) is not None,
        )
        if problem:
            raise ValueError(problem)
        with session_factory() as session:
            checks = (("NYSE closures", "closed", parsed.closed),
                      ("NYSE early closes", "early_close", parsed.early))  # fmt: skip
            for label, kind, fresh in checks:
                stored = set(session.scalars(
                    select(MarketCalendarDay.day).where(MarketCalendarDay.kind == kind)
                ))  # fmt: skip
                problem = ec.check_dates(label, sorted(fresh), stored, today)
                if problem:
                    raise ValueError(problem)
            first = date(min(parsed.years), 1, 1)
            session.execute(delete(MarketCalendarDay).where(MarketCalendarDay.day >= first))
            session.add_all(MarketCalendarDay(day=d, kind="closed", name=name)
                            for d, name in parsed.closed.items())  # fmt: skip
            session.add_all(MarketCalendarDay(day=d, kind="early_close",
                                              close_time=close.strftime("%H:%M"))
                            for d, close in parsed.early.items())  # fmt: skip
            _ok(session, NYSE, now)
            session.commit()
    except Exception as e:  # noqa: BLE001
        return _fail(session_factory, notify, NYSE, e, now)
    load_calendar(session_factory)
    return None


def _fred_key(session_factory) -> str:
    try:
        return settings.get(session_factory, "fred_api_key").strip()
    except Exception:  # noqa: BLE001 -- no key readable: FRED is left out
        logger.exception("couldn't read the FRED key")
        return ""


def refresh_all(session_factory, now, fetchers: Fetchers | None = None, notify=None,
                only: Iterable[str] | None = None) -> dict[str, str | None]:  # fmt: skip
    """Refresh every source (or `only` those), each on its own: {source: error or None}.
    FRED only runs with a key. Never raises."""
    fetchers = fetchers or Fetchers()
    wanted = set(SOURCES if only is None else only)
    key = _fred_key(session_factory)
    results: dict[str, str | None] = {}
    if FOREXFACTORY in wanted:
        results[FOREXFACTORY] = refresh_forexfactory(
            session_factory, now, fetchers.forexfactory, notify
        )
    if FED in wanted:
        results[FED] = refresh_fomc(session_factory, now, fetchers.fomc, notify)
    if FRED_CPI in wanted and key:
        results[FRED_CPI] = refresh_fred(session_factory, now, fetchers.fred, key, notify)
    if NYSE in wanted:
        results[NYSE] = refresh_nyse(session_factory, now, fetchers.nyse, notify)
    return results


def _sunday(today: date) -> date:
    return date_lists.ff_week(today)[0]


def _ff_covers(session, now: datetime) -> bool:
    """ForexFactory refreshed at or after this week's Sunday 00:00 New York, and has a
    row in the week: a timestamp alone isn't proof (date_sets._ff_covers)."""
    row = session.get(EconomicEventSource, FOREXFACTORY)
    if row is None or row.refreshed_at is None:
        return False
    sunday, saturday = date_lists.ff_week(_today(now))
    if row.refreshed_at < datetime.combine(sunday, time(0), MARKET_TZ):
        return False
    found = session.scalar(
        select(EconomicEvent.id)
        .where(
            EconomicEvent.source == FOREXFACTORY,
            EconomicEvent.event_date >= sunday,
            EconomicEvent.event_date <= saturday,
        )
        .limit(1)
    )
    return found is not None


def due_sources(session_factory, now: datetime) -> list[str]:
    """The sources to refresh now: ForexFactory until it covers the current week
    (from Sunday 18:00 New York, when the new week's feed is out), the others a
    week after their last good refresh, FRED only with a key. A source that was
    tried in the last hour waits."""
    today = _today(now)
    local = now.astimezone(MARKET_TZ)
    has_key = bool(_fred_key(session_factory))
    due = []
    with session_factory() as session:
        for source in SOURCES:
            if source == FRED_CPI and not has_key:
                continue
            row = session.get(EconomicEventSource, source)
            if row is not None and row.last_attempt_at and now - row.last_attempt_at < RETRY_AFTER:
                continue
            if source == FOREXFACTORY:
                sunday_early = today == _sunday(today) and local.time() < FF_NEW_WEEK_AT
                if not _ff_covers(session, now) and not sunday_early:
                    due.append(source)
            elif row is None or row.refreshed_at is None or now - row.refreshed_at > REFRESH_EVERY:
                due.append(source)
    return due


# Set once the background loop's first check has finished, so the clock doesn't
# alert that a list is missing while the app is still fetching it at startup.
FIRST_CHECK_DONE = threading.Event()


# One refresh at a time: the background loop and Refresh now share it.
REFRESH_LOCK = threading.Lock()


def refreshing() -> bool:
    return REFRESH_LOCK.locked()


def try_refresh_all(session_factory, now, fetchers=None, notify=None, only=None):
    """refresh_all unless one is already running (then None). Never raises."""
    if not REFRESH_LOCK.acquire(blocking=False):
        return None
    try:
        return refresh_all(session_factory, now, fetchers, notify, only)
    except Exception:  # noqa: BLE001 -- a background refresh never dies silently
        logger.exception("market calendar refresh failed")
        return {}
    finally:
        REFRESH_LOCK.release()


def refresh_if_due(session_factory, now, fetchers=None, notify=None) -> None:
    """Refresh whatever is due (the background loop). Never raises."""
    try:
        sources = due_sources(session_factory, now)
        if sources:
            try_refresh_all(session_factory, now, fetchers, notify, only=sources)
    except Exception:  # noqa: BLE001 -- the loop carries on
        logger.exception("market calendar refresh failed")
    finally:
        FIRST_CHECK_DONE.set()


def load_calendar(session_factory) -> None:
    """Hand the stored NYSE days to core/sessions. Unreadable: the table stays."""
    try:
        with session_factory() as session:
            rows = list(session.scalars(select(MarketCalendarDay)))
        closed = [r.day for r in rows if r.kind == "closed"]
        early = {
            r.day: time.fromisoformat(r.close_time or "13:00")
            for r in rows
            if r.kind == "early_close"
        }
        sessions.use_fetched_calendar(closed, early)
    except Exception:  # noqa: BLE001 -- fall back to the hard-coded table
        logger.exception("couldn't load the stored market calendar; using the built-in table")


def day_notes(session_factory, day: date) -> dict:
    """The schedule drawer's top (#221): the day's economic events as chips
    (core/economic_calendar.day_items), the holiday's name when NYSE is closed, and
    whether the holiday calendar covers the day at all."""
    with session_factory() as session:
        rows = list(session.scalars(select(EconomicEvent).where(EconomicEvent.event_date == day)))
        market = session.get(MarketCalendarDay, day)
    return {
        "events": ec.day_items(rows),
        "holiday": market.name if market and market.kind == "closed" else None,
        "calendar_covers": sessions.calendar_covers(day),
    }


def _has_row(session, source: str, key: str, day: date) -> bool:
    found = select(EconomicEvent.id).where(
        EconomicEvent.source == source, EconomicEvent.key == key, EconomicEvent.event_date == day
    )
    return session.scalar(found.limit(1)) is not None


def _backing_available(session, name: str, now: datetime) -> bool:
    """The Fed's or FRED's rows count while refreshed in the last 21 days and
    holding a date still ahead (date_sets._backing_available)."""
    source, status = BACKING[name]
    row = session.get(EconomicEventSource, status)
    if row is None or row.refreshed_at is None or now - row.refreshed_at > AVAILABLE_FOR:
        return False
    ahead = session.scalar(
        select(EconomicEvent.id)
        .where(
            EconomicEvent.source == source,
            EconomicEvent.key == name,
            EconomicEvent.event_date >= _today(now),
        )
        .limit(1)
    )
    return ahead is not None


def _member(session, name: str, day: date, now: datetime) -> bool | None:
    if name == "EOM":
        return date_lists.eom_member(day)
    if name == "HOLIDAY":
        return date_lists.holiday_member(day)
    if name not in BACKING:
        return None
    sunday, saturday = date_lists.ff_week(_today(now))
    backing = _backing_available(session, name, now)
    if sunday <= day <= saturday and _ff_covers(session, now):
        # Either fresh source saying yes is enough: a ForexFactory title that changed
        # mustn't quietly drop a Fed or FRED day from a blackout.
        return _has_row(session, FOREXFACTORY, name, day) or (
            backing and _has_row(session, BACKING[name][0], name, day)
        )
    if backing:
        return _has_row(session, BACKING[name][0], name, day)
    return None


def ff_disagreements(session_factory, names: Iterable[str], now: datetime) -> list:
    """(list, day) for each Fed or FRED day in the week ForexFactory covers that
    ForexFactory has no matching row for: its titles may have changed. Never raises."""
    found = []
    try:
        with session_factory() as session:
            if not _ff_covers(session, now):
                return []
            sunday, saturday = date_lists.ff_week(_today(now))
            for name in sorted(set(names) & set(BACKING)):
                if not _backing_available(session, name, now):
                    continue
                source = BACKING[name][0]
                days = session.scalars(
                    select(EconomicEvent.event_date).where(
                        EconomicEvent.source == source,
                        EconomicEvent.key == name,
                        EconomicEvent.event_date >= sunday,
                        EconomicEvent.event_date <= saturday,
                    )
                )
                for day in sorted(set(days)):
                    if not _has_row(session, FOREXFACTORY, name, day):
                        found.append((name, day))
    except Exception:  # noqa: BLE001
        logger.exception("couldn't compare ForexFactory with the Fed and FRED")
    return found


def is_member(session_factory, name: str, day: date, now: datetime) -> bool | None:
    """Whether `day` is in list `name`: ForexFactory decides the week it has
    refreshed into, the Fed (FOMC) or FRED (CPI) any other day while fresh;
    otherwise None, it can't tell (date_sets.is_member). Never raises."""
    try:
        with session_factory() as session:
            return _member(session, name, day, now)
    except Exception:  # noqa: BLE001 -- a lookup that fails can't tell
        logger.exception("couldn't look up the %s list for %s", name, day)
        return None


def lists_for(session_factory, names: Iterable[str], day: date, now: datetime) -> dict:
    """{name: True, False or None} for `day`."""
    return {name: is_member(session_factory, name, day, now) for name in names}


def lists_ahead(session_factory, names: Iterable[str], days: Iterable[date], now: datetime) -> dict:
    """{name: {day: True, False or None}} for the days after an entry day, for the Before
    expiration lists (#207), in one session. A lookup that fails can't tell (None)."""
    names, days = list(names), list(days)
    if not names:
        return {}
    found = {name: dict.fromkeys(days) for name in names}
    try:
        with session_factory() as session:
            for name in names:
                for day in days:
                    found[name][day] = _member(session, name, day, now)
    except Exception:  # noqa: BLE001 -- the days not yet looked up stay None
        logger.exception("couldn't look up the lists ahead")
    return found


def _upcoming(session, name: str, now: datetime) -> list[date]:
    """Dates from today: ForexFactory's for the week it covers, the Fed's or FRED's
    for every other day (date_sets.upcoming)."""
    today = _today(now)
    if name == "EOM":
        return date_lists.eom_upcoming(today)
    if name == "HOLIDAY":
        return date_lists.holiday_upcoming(today)
    source = BACKING[name][0]

    def dates(src, end=None):
        query = select(EconomicEvent.event_date).where(
            EconomicEvent.source == src,
            EconomicEvent.key == name,
            EconomicEvent.event_date >= today,
        )
        if end is not None:
            query = query.where(EconomicEvent.event_date <= end)
        return set(session.scalars(query))

    backing = dates(source)
    if not _ff_covers(session, now):
        return sorted(backing)
    sunday, saturday = date_lists.ff_week(today)
    return sorted(
        {d for d in backing if not sunday <= d <= saturday} | dates(FOREXFACTORY, saturday)
    )


def summary(session_factory, now: datetime) -> dict:
    """What the Automation page's Market calendar panel shows."""
    with session_factory() as session:
        lists = [
            {"name": name, "label": date_lists.LABELS[name],
             "upcoming": _upcoming(session, name, now),
             "available": _member(session, name, _today(now), now) is not None}
            for name in date_lists.DATE_SETS
        ]  # fmt: skip
        sources = []
        for source in SOURCES:
            row = session.get(EconomicEventSource, source)
            sources.append({
                "source": source, "label": LABELS[source],
                "refreshed_at": row.refreshed_at if row else None,
                "attempted_at": row.last_attempt_at if row else None,
                "error": row.last_error if row else None,
            })  # fmt: skip
        years = sorted({d.year for d in session.scalars(select(MarketCalendarDay.day))})
    return {"lists": lists, "sources": sources, "calendar_years": years,
            "calendar_through": sessions.calendar_through(),
            "fred_key": bool(_fred_key(session_factory))}  # fmt: skip
