from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from optionsjourney.core import sessions
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import EconomicEvent, EconomicEventSource, MarketCalendarDay
from optionsjourney.services import economic_events as ee
from optionsjourney.services import settings
from tests.core.test_economic_calendar import FF, NYSE_NOTES, NYSE_ROWS, nyse_page, panel

TODAY = date(2026, 10, 13)  # a Tuesday
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=MARKET_TZ)
KEY = "a1" * 16
MEETINGS_2026 = [("January", "27-28"), ("March", "17-18*"), ("April", "28-29"), ("June", "16-17*"),
                 ("July", "28-29"), ("September", "15-16*"), ("October", "27-28"),
                 ("December", "8-9*")]  # fmt: skip
MEETINGS_2027 = [("January", "26-27"), ("March", "16-17*"), ("April", "27-28"), ("June", "8-9*"),
                 ("July", "27-28"), ("September", "14-15*"), ("October", "26-27"),
                 ("December", "7-8*")]  # fmt: skip


def fed_page(meetings_2026=MEETINGS_2026):
    return panel(2026, meetings_2026) + panel(2027, MEETINGS_2027)


FED = fed_page()


@pytest.fixture(autouse=True)
def table_only():
    sessions.use_fetched_calendar([], {})
    ee.reset_alerts()
    yield
    sessions.use_fetched_calendar([], {})


@pytest.fixture
def alerts():
    return []


class Fetch:
    """Fake fetchers: each returns what the test set, or raises it."""

    def __init__(self):
        self.ff, self.fed, self.nyse = FF, FED, nyse_page(NYSE_ROWS, NYSE_NOTES)
        self.fred = {"release_dates": [{"date": "2026-10-14"}, {"date": "2026-11-10"},
                                       {"date": "2026-12-10"}]}  # fmt: skip
        self.calls = []

    def _give(self, name, value):
        self.calls.append(name)
        if isinstance(value, Exception):
            raise value
        return value

    def fetchers(self):
        return ee.Fetchers(
            forexfactory=lambda: self._give("forexfactory", self.ff),
            fomc=lambda: self._give("fed", self.fed),
            fred=lambda release, key, start: self._give("fred", self.fred),
            nyse=lambda: self._give("nyse", self.nyse),
        )


@pytest.fixture
def fetch():
    return Fetch()


def refresh(factory, fetch, alerts, now=NOW):
    return ee.refresh_all(factory, now, fetch.fetchers(), notify=alerts.append)


def rows(factory, source):
    with factory() as session:
        return sorted(
            (r.event_date, r.key)
            for r in session.scalars(select(EconomicEvent).where(EconomicEvent.source == source))
        )


def status(factory, source):
    with factory() as session:
        return session.get(EconomicEventSource, source)


def test_a_refresh_stores_each_source(factory, fetch, alerts):
    settings.save_section(factory, "market_calendar", {"fred_api_key": KEY})
    assert refresh(factory, fetch, alerts) == {"forexfactory": None, "fed": None,
                                                "fred:CPI": None, "nyse": None}  # fmt: skip
    assert (date(2026, 10, 14), "CPI") in rows(factory, "forexfactory")
    # Fed and FRED keep dates from today on
    assert rows(factory, "fed")[:2] == [(date(2026, 10, 28), "FOMC"), (date(2026, 12, 9), "FOMC")]
    assert len(rows(factory, "fed")) == 10  # and 2027's eight
    assert rows(factory, "fred")[0] == (date(2026, 10, 14), "CPI")
    assert status(factory, "nyse").refreshed_at is not None and alerts == []
    with factory() as session:
        assert session.get(MarketCalendarDay, date(2028, 7, 3)).close_time == "13:00"
    # and the market now follows NYSE's page for 2028
    assert sessions.calendar_covers(date(2028, 5, 1))
    assert sessions.session_close(date(2028, 7, 3)) == time(13, 0)


def test_without_a_fred_key_fred_is_left_out(factory, fetch, alerts):
    result = refresh(factory, fetch, alerts)
    assert "fred:CPI" not in result and "fred" not in fetch.calls
    assert status(factory, "fred:CPI") is None


def test_a_refresh_that_drops_a_stored_future_date_keeps_the_old_rows(factory, fetch, alerts):
    refresh(factory, fetch, alerts)
    fetch.fed = fed_page([m for m in MEETINGS_2026 if m[0] != "October"])  # it vanishes
    later = NOW + timedelta(days=1)
    result = refresh(factory, fetch, alerts, later)
    assert result["fed"] is not None and "2026-10-28" in result["fed"]
    assert (date(2026, 10, 28), "FOMC") in rows(factory, "fed")
    row = status(factory, "fed")
    assert row.last_error == result["fed"] and row.refreshed_at < later
    assert [a.title for a in alerts] == ["Market calendar: FOMC refresh failed"]
    assert "Kept the dates it had" in alerts[0].detail


def test_a_failed_fetch_keeps_rows_and_alerts_once_a_day(factory, fetch, alerts):
    refresh(factory, fetch, alerts)
    fetch.nyse = RuntimeError("timed out")
    refresh(factory, fetch, alerts, NOW + timedelta(hours=2))
    refresh(factory, fetch, alerts, NOW + timedelta(hours=3))
    assert len([a for a in alerts if "NYSE" in a.title]) == 1
    with factory() as session:
        assert session.get(MarketCalendarDay, date(2028, 7, 3)) is not None
    assert status(factory, "nyse").last_error == "timed out"


def test_forexfactory_refuses_a_feed_for_another_week(factory, fetch, alerts):
    next_week = NOW + timedelta(days=7)
    result = refresh(factory, fetch, alerts, next_week)
    assert "not the current week" in result["forexfactory"]
    assert rows(factory, "forexfactory") == []


def test_forexfactory_replaces_only_its_own_span(factory, fetch, alerts):
    with factory() as session:
        session.add(EconomicEvent(event_date=date(2026, 10, 2), title="old", impact="high",
                                  key="CPI", source="forexfactory"))  # fmt: skip
        session.add(EconomicEvent(event_date=date(2026, 10, 12), title="gone", impact="high",
                                  key="CPI", source="forexfactory"))  # fmt: skip
        session.commit()
    refresh(factory, fetch, alerts)
    found = rows(factory, "forexfactory")
    assert (date(2026, 10, 2), "CPI") in found and (date(2026, 10, 12), "CPI") not in found


def test_the_fred_key_never_shows_in_an_error(factory, fetch, alerts):
    settings.save_section(factory, "market_calendar", {"fred_api_key": KEY})
    fetch.fred = RuntimeError(f"GET https://api.stlouisfed.org/x?api_key={KEY}&f=1 refused")
    result = refresh(factory, fetch, alerts)
    assert KEY not in result["fred:CPI"] and "api_key=***" in result["fred:CPI"]
    assert KEY not in status(factory, "fred:CPI").last_error
    assert all(KEY not in a.detail for a in alerts)


def test_the_stored_calendar_is_loaded_at_startup(factory):
    with factory() as session:
        session.add(MarketCalendarDay(day=date(2028, 1, 17), kind="closed", name="MLK"))
        session.add(MarketCalendarDay(day=date(2028, 7, 3), kind="early_close",
                                      close_time="13:00"))  # fmt: skip
        session.commit()
    ee.load_calendar(factory)
    assert sessions.session_close(date(2028, 1, 17)) is None
    assert sessions.session_close(date(2028, 7, 3)) == time(13, 0)


def test_the_drawers_notes_for_a_day(factory, fetch, alerts):
    refresh(factory, fetch, alerts)
    cpi_day = ee.day_notes(factory, date(2026, 10, 14))
    cpi = next(i for i in cpi_day["events"] if i["key"] == "CPI")
    assert cpi["time"] == "08:30" and {p["forecast"] for p in cpi["prints"]} == {"0.3%"}
    assert cpi_day["holiday"] is None and cpi_day["calendar_covers"]
    assert ee.day_notes(factory, date(2026, 11, 26))["holiday"] == "Thanksgiving Day"  # NYSE's
    assert ee.day_notes(factory, date(2026, 11, 27))["holiday"] is None
    assert not ee.day_notes(factory, date(2035, 3, 1))["calendar_covers"]


# Membership (date_sets.is_member)


def add(factory, source, key, day):
    with factory() as session:
        session.add(EconomicEvent(event_date=day, title=key, impact="high", key=key,
                                  source=source))  # fmt: skip
        session.commit()


def refreshed(factory, source, at):
    with factory() as session:
        row = session.get(EconomicEventSource, source) or EconomicEventSource(source=source)
        row.refreshed_at = row.last_attempt_at = at
        session.add(row)
        session.commit()


SUNDAY = datetime(2026, 10, 11, 0, 0, tzinfo=MARKET_TZ)


def test_forexfactory_decides_only_a_week_it_has_refreshed_into(factory):
    add(factory, "forexfactory", "CPI", date(2026, 10, 14))
    add(factory, "fred", "CPI", date(2026, 10, 15))  # FRED disagrees about this week
    add(factory, "fred", "CPI", date(2026, 11, 10))
    refreshed(factory, "fred:CPI", NOW - timedelta(days=3))
    # ForexFactory last refreshed before this Sunday: FRED decides
    refreshed(factory, "forexfactory", SUNDAY - timedelta(minutes=1))
    assert ee.is_member(factory, "CPI", date(2026, 10, 15), NOW) is True
    assert ee.is_member(factory, "CPI", date(2026, 10, 14), NOW) is False
    # Refreshed since Sunday, with a row in the week: it answers this week too, and a
    # day either fresh source has is in the list (a renamed title can't drop one)
    refreshed(factory, "forexfactory", SUNDAY + timedelta(hours=1))
    assert ee.is_member(factory, "CPI", date(2026, 10, 14), NOW) is True
    assert ee.is_member(factory, "CPI", date(2026, 10, 15), NOW) is True
    assert ee.is_member(factory, "CPI", date(2026, 10, 16), NOW) is False
    # ...and FRED still decides the weeks it doesn't cover
    assert ee.is_member(factory, "CPI", date(2026, 11, 10), NOW) is True


def test_a_forexfactory_timestamp_without_rows_in_the_week_is_not_coverage(factory):
    add(factory, "forexfactory", "CPI", date(2026, 10, 7))  # last week's
    refreshed(factory, "forexfactory", SUNDAY + timedelta(hours=1))
    assert ee.is_member(factory, "CPI", date(2026, 10, 14), NOW) is None


def test_fed_and_fred_count_within_21_days_and_with_a_date_ahead(factory):
    add(factory, "fed", "FOMC", date(2026, 10, 28))
    refreshed(factory, "fed", NOW - timedelta(days=20))
    assert ee.is_member(factory, "FOMC", date(2026, 10, 28), NOW) is True
    assert ee.is_member(factory, "FOMC", date(2026, 10, 27), NOW) is False
    refreshed(factory, "fed", NOW - timedelta(days=22))
    assert ee.is_member(factory, "FOMC", date(2026, 10, 28), NOW) is None
    refreshed(factory, "fed", NOW)
    assert ee.is_member(factory, "FOMC", date(2026, 12, 9), NOW + timedelta(days=30)) is None


def test_end_of_month_needs_no_data(factory):
    assert ee.is_member(factory, "EOM", date(2026, 10, 30), NOW) is True
    assert ee.is_member(factory, "EOM", date(2029, 10, 31), NOW) is None


def test_lists_for_a_day(factory):
    add(factory, "fed", "FOMC", date(2026, 10, 13))
    refreshed(factory, "fed", NOW)
    assert ee.lists_for(factory, ("FOMC", "CPI", "EOM"), TODAY, NOW) == {
        "FOMC": True,
        "CPI": None,
        "EOM": False,
    }


def test_market_holidays_need_no_data(factory):
    """#207: weekdays NYSE is shut, from the same calendar as end of month."""
    assert ee.is_member(factory, "HOLIDAY", date(2026, 11, 26), NOW) is True
    assert ee.is_member(factory, "HOLIDAY", date(2026, 11, 27), NOW) is False  # a half day
    assert ee.is_member(factory, "HOLIDAY", date(2029, 11, 22), NOW) is None


def test_lists_ahead_answer_each_day_after_the_entry(factory):
    """#207: the days a trade would be open, for the Before expiration lists."""
    add(factory, "fed", "FOMC", date(2026, 10, 28))
    refreshed(factory, "fed", NOW)
    days = [date(2026, 10, 27), date(2026, 10, 28)]
    assert ee.lists_ahead(factory, ("FOMC", "CPI"), days, NOW) == {
        "FOMC": {date(2026, 10, 27): False, date(2026, 10, 28): True},
        "CPI": {date(2026, 10, 27): None, date(2026, 10, 28): None},
    }
    assert ee.lists_ahead(factory, (), days, NOW) == {}


# When to refresh


def test_a_refresh_is_due_until_forexfactory_covers_the_week(factory, fetch, alerts):
    assert ee.due_sources(factory, NOW) == ["forexfactory", "fed", "nyse"]
    refresh(factory, fetch, alerts)
    assert ee.due_sources(factory, NOW + timedelta(hours=2)) == []
    # Next Sunday evening ForexFactory's new week is due; the rest a week after they ran
    sunday_evening = datetime(2026, 10, 18, 18, 30, tzinfo=MARKET_TZ)
    assert ee.due_sources(factory, sunday_evening) == ["forexfactory"]
    assert ee.due_sources(factory, sunday_evening - timedelta(hours=1)) == []
    assert ee.due_sources(factory, NOW + timedelta(days=7, hours=1)) == [
        "forexfactory",
        "fed",
        "nyse",
    ]


def test_a_failing_source_is_tried_again_after_an_hour(factory, fetch, alerts):
    fetch.fed = RuntimeError("down")
    refresh(factory, fetch, alerts)
    assert ee.due_sources(factory, NOW + timedelta(minutes=30)) == []
    assert ee.due_sources(factory, NOW + timedelta(minutes=61)) == ["fed"]


def test_refresh_if_due_runs_only_what_is_due(factory, fetch, alerts):
    ee.FIRST_CHECK_DONE.clear()
    refresh(factory, fetch, alerts)
    assert not ee.FIRST_CHECK_DONE.is_set()  # only the background check sets it
    fetch.calls.clear()
    ee.refresh_if_due(factory, NOW + timedelta(hours=2), fetch.fetchers(), alerts.append)
    assert fetch.calls == [] and ee.FIRST_CHECK_DONE.is_set()
    ee.refresh_if_due(factory, NOW + timedelta(days=8), fetch.fetchers(), alerts.append)
    assert fetch.calls == ["forexfactory", "fed", "nyse"]


def test_summaries_for_the_panel(factory, fetch, alerts):
    refresh(factory, fetch, alerts)
    found = ee.summary(factory, NOW)
    lists = {item["name"]: item for item in found["lists"]}
    assert lists["FOMC"]["upcoming"][:2] == [date(2026, 10, 28), date(2026, 12, 9)]
    assert lists["FOMC"]["available"] and lists["EOM"]["available"]
    assert lists["CPI"]["upcoming"] == [date(2026, 10, 14)]  # this week only, no FRED key
    assert lists["HOLIDAY"]["available"] and lists["HOLIDAY"]["label"] == "Market holidays"
    assert lists["HOLIDAY"]["upcoming"][0] == date(2026, 11, 26)
    sources = {item["source"]: item for item in found["sources"]}
    assert sources["nyse"]["refreshed_at"] is not None and sources["nyse"]["error"] is None
    assert found["calendar_years"] == [2026, 2027, 2028]
    assert found["fred_key"] is False


# Final review fixes


def test_the_fred_key_never_reaches_the_log(factory, caplog):
    import logging

    ee.quiet_http_logs()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING

    def fetch(release, key, start):  # what httpx logs for each request
        logging.getLogger("httpx").info(
            "HTTP Request: GET %s?api_key=%s&file_type=json", ee.ec.FRED_URL, key
        )
        return {"release_dates": [{"date": "2026-10-14"}]}

    caplog.set_level(logging.INFO, logger="httpx")  # even if someone turns httpx back up
    assert ee.refresh_fred(factory, NOW, fetch, KEY) is None
    assert "api_key=***" in caplog.text and KEY not in caplog.text


def nyse(rows=NYSE_ROWS, notes=NYSE_NOTES):
    return nyse_page(rows, notes)


@pytest.mark.parametrize(
    "rows, notes, why",
    [
        (NYSE_ROWS, [n for n in NYSE_NOTES if "close early" not in n],
         "early closes for 2026 don't match"),  # a reworded footnote
        ([r if r[0] != "Good Friday" else [r[0], "Friday April 3", *r[2:]] for r in NYSE_ROWS],
         NYSE_NOTES, "closures for 2026 don't match"),  # one holiday unreadable
        ([*NYSE_ROWS, ["Stray", "Monday, March 2", "—", "—"]], NYSE_NOTES,
         "closures for 2026 don't match"),  # a stray date read as a closure
        (NYSE_ROWS, [n.replace("Monday, July 3, 2028", "July 3")
                     .replace("and Friday, November 24, 2028", "") for n in NYSE_NOTES],
         "no early close for 2028"),
    ],
)  # fmt: skip
def test_a_first_nyse_fetch_must_match_the_built_in_table(factory, alerts, rows, notes, why):
    result = ee.refresh_nyse(factory, NOW, lambda: nyse(rows, notes), alerts.append)
    assert result is not None and why in result
    with factory() as session:
        assert session.scalar(select(MarketCalendarDay.day).limit(1)) is None
    assert sessions.session_close(date(2026, 11, 27)) == time(13, 0)  # the table stands
    assert alerts and "NYSE" in alerts[0].title


def test_a_new_closure_in_the_coming_week_is_refused(factory, alerts):
    rows = [*NYSE_ROWS, ["Stray", "—", "—", "Friday, March 3"]]
    soon = datetime(2028, 3, 1, 9, 0, tzinfo=MARKET_TZ)
    result = ee.refresh_nyse(factory, soon, lambda: nyse(rows), alerts.append)
    assert "2028-03-03" in result and "next 7 days" in result
    later = ee.refresh_nyse(factory, NOW, lambda: nyse(rows), alerts.append)
    assert later is None  # months ahead it's taken as NYSE's word


def test_forexfactory_missing_a_fed_day_in_its_week_is_reported(factory):
    add(factory, "forexfactory", "CPI", date(2026, 10, 14))
    add(factory, "fed", "FOMC", date(2026, 10, 15))
    refreshed(factory, "fed", NOW)
    refreshed(factory, "forexfactory", SUNDAY + timedelta(hours=1))
    assert ee.ff_disagreements(factory, ("FOMC", "CPI"), NOW) == [("FOMC", date(2026, 10, 15))]
    add(factory, "forexfactory", "FOMC", date(2026, 10, 15))
    assert ee.ff_disagreements(factory, ("FOMC",), NOW) == []


def test_only_one_refresh_runs_at_a_time(factory, fetch, alerts):
    with ee.REFRESH_LOCK:
        assert ee.try_refresh_all(factory, NOW, fetch.fetchers(), alerts.append) is None
    assert fetch.calls == []
    assert ee.try_refresh_all(factory, NOW, fetch.fetchers(), alerts.append) is not None


def test_a_failure_whose_status_cant_be_written_still_alerts_and_never_raises(alerts):
    def broken():
        raise RuntimeError("database locked")

    assert ee.refresh_fomc(broken, NOW, lambda: "<html>", alerts.append) is not None
    assert alerts and "FOMC" in alerts[0].title
