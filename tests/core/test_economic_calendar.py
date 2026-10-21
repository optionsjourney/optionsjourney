"""The calendar parsers, on trimmed copies of the real pages' structure
(ForexFactory's export, the Fed's FOMC page, FRED, NYSE's hours page)."""

import json
from datetime import date, time
from types import SimpleNamespace

import pytest

from optionsjourney.core import economic_calendar as ec

FF = [
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High",
     "forecast": "0.3%", "previous": "0.4%"},
    {"title": "Core CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00",
     "impact": "High", "forecast": "0.3%", "previous": "0.3%"},
    {"title": "S&P Global Services PMI", "country": "USD", "date": "2026-10-13T09:45:00-04:00",
     "impact": "Medium", "forecast": "", "previous": "54.1"},
    {"title": "President Speaks", "country": "USD", "date": "2026-10-12T19:55:00-04:00",
     "impact": "Medium", "forecast": "", "previous": ""},
    {"title": "President Speaks", "country": "USD", "date": "2026-10-12T19:55:00-04:00",
     "impact": "Medium", "forecast": "", "previous": ""},
    {"title": "Bank Holiday", "country": "USD", "date": "2026-10-12T00:00:00-04:00",
     "impact": "Holiday", "forecast": "", "previous": ""},
    {"title": "Treasury Currency Report", "country": "USD", "date": "2026-10-15T00:00:00-04:00",
     "impact": "Medium", "forecast": "", "previous": ""},
    {"title": "Crude Oil Inventories", "country": "USD", "date": "2026-10-14T10:30:00-04:00",
     "impact": "Low", "forecast": "", "previous": ""},
    {"title": "CPI y/y", "country": "GBP", "date": "2026-10-11T02:00:00-04:00", "impact": "High",
     "forecast": "", "previous": ""},
    {"title": "German ZEW", "country": "EUR", "date": "2026-10-17T05:00:00-04:00",
     "impact": "High", "forecast": "", "previous": ""},
]  # fmt: skip


def test_forexfactory_keeps_usd_high_and_medium_and_tags_the_lists():
    parsed = ec.parse_forexfactory(FF)
    assert parsed.span == (date(2026, 10, 11), date(2026, 10, 17))  # over every event
    assert parsed.usd_count == 8
    rows = {(r["event_date"].isoformat(), r["event_time"], r["title"]): r for r in parsed.rows}
    assert set(rows) == {
        ("2026-10-14", "08:30", "CPI m/m"),
        ("2026-10-14", "08:30", "Core CPI m/m"),
        ("2026-10-13", "09:45", "S&P Global Services PMI"),
        ("2026-10-12", "19:55", "President Speaks"),  # the duplicate is dropped
        ("2026-10-15", None, "Treasury Currency Report"),  # 00:00 is all day
    }
    cpi = rows[("2026-10-14", "08:30", "CPI m/m")]
    assert (cpi["key"], cpi["impact"], cpi["source"]) == ("CPI", "high", "forexfactory")
    assert (cpi["forecast"], cpi["previous"]) == ("0.3%", "0.4%")
    pmi = rows[("2026-10-13", "09:45", "S&P Global Services PMI")]
    assert (pmi["forecast"], pmi["previous"]) == (None, "54.1")  # a blank figure is None
    assert rows[("2026-10-13", "09:45", "S&P Global Services PMI")]["key"] is None


@pytest.mark.parametrize(
    "title, key",
    [("Federal Funds Rate", "FOMC"), ("FOMC Statement", "FOMC"),
     ("FOMC Press Conference", "FOMC"), ("Core CPI m/m", "CPI"), ("CPI y/y", "CPI"),
     ("PPI m/m", None), ("FOMC Meeting Minutes", None), ("Non-Farm Employment Change", None)],
)  # fmt: skip
def test_titles_to_lists(title, key):
    assert ec.key_for_title(title) == key


def test_forexfactory_refuses_what_is_not_a_list_or_has_a_bad_date():
    with pytest.raises(ValueError):
        ec.parse_forexfactory({"error": "rate limited"})
    with pytest.raises(ValueError):
        ec.parse_forexfactory([{"title": "x", "country": "USD", "date": "soon"}])


def meeting(month, dates, shaded=False):
    extra = "fomc-meeting--shaded " if shaded else ""
    return (
        f'<div class="{extra}row fomc-meeting" ">\n'
        f'  <div class="{extra}fomc-meeting__month col-xs-5"><strong>{month}</strong></div>\n'
        f'  <div class="fomc-meeting__date col-xs-4">{dates}</div>\n'
        '  <div class="col-xs-12"><strong>Statement:</strong><br>'
        '<a href="/x.pdf">PDF</a></div>\n</div>\n'
    )


def panel(year, meetings):
    head = (
        '<div class="panel panel-default"><div class="panel-heading">'
        f'<h4><a id="1">{year} FOMC Meetings</a></h4></div>'
    )
    return head + "".join(meeting(*m) for m in meetings) + "</div>"


FED = (
    '<p><a href="#1">2026</a> | <a href="#2">2025</a><br />Future Year: <a href="#3">2027</a></p>'
    + panel(2026, [("January", "27-28"), ("March", "17-18*", True), ("April", "28-29"),
                   ("June", "16-17*", True), ("July", "28-29"), ("September", "15-16*", True),
                   ("October", "27-28"), ("December", "8-9*", True)])
    + panel(2025, [("January", "28-29"), ("August", "22 (notation vote)"), ("March", "18-19*")])
    + panel(2024, [("Apr/May", "30-1"), ("Dec/Jan", "31-1")])
)  # fmt: skip


def test_fomc_days_are_day_two_of_each_meeting():
    years = ec.parse_fomc(FED)
    assert [d.isoformat() for d in years[2026]] == [
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
        "2026-10-28", "2026-12-09",
    ]  # fmt: skip
    assert years[2025] == [date(2025, 1, 29), date(2025, 3, 19)]  # the notation vote skipped
    assert years[2024] == [date(2024, 5, 1), date(2025, 1, 1)]  # Apr/May; Dec/Jan next year


def test_fred_release_dates():
    payload = {"release_dates": [{"release_id": 10, "date": "2026-10-14"},
                                 {"release_id": 10, "date": "2026-10-14"},
                                 {"release_id": 10, "date": "2026-09-11"}]}  # fmt: skip
    assert ec.parse_fred_dates(payload) == [date(2026, 9, 11), date(2026, 10, 14)]
    with pytest.raises(ValueError, match="Bad Request"):
        ec.parse_fred_dates({"error_message": "Bad Request. The api_key is not registered."})


def nyse_page(rows, footnotes):
    table = {
        "id": "t1",
        "name": "cms-component-table",
        "props": {
            "sections": [
                {"rows": [{"cells": [{"text": t} for t in row]} for row in rows]},
            ]
        },
    }
    notes = [{"id": f"n{k}", "name": "cms-component-text", "props": {"text": text}}
             for k, text in enumerate(footnotes)]  # fmt: skip
    data = json.dumps([table, *notes], separators=(",", ":"), ensure_ascii=False)
    return f"<script>self.__next_f.push({data})</script>"


NYSE_ROWS = [
    ["Holiday", "2026", "2027", "2028"],
    ["New Year’s Day", "Thursday, January 1", "Friday, January 1", "—*"],
    ["Martin Luther King, Jr. Day", "Monday, January 19", "Monday, January 18",
     "Monday, January 17"],
    ["Washington's Birthday", "Monday, February 16", "Monday, February 15", "Monday, February 21"],
    ["Good Friday", "Friday, April 3", "Friday, March 26", "Friday, April 14"],
    ["Memorial Day", "Monday, May 25", "Monday, May 31", "Monday, May 29"],
    ["Juneteenth National Independence Day", "Friday, June 19",
     "Friday, June 18 (Juneteenth National Independence Day observed)", "Monday, June 19"],
    ["Independence Day", "Friday, July 3 (Independence Day observed) ",
     "Monday, July 5 (Independence Day observed) ", "Tuesday, July 4**"],
    ["Labor Day", "Monday, September 7", "Monday, September 6", "Monday, September 4"],
    ["Thanksgiving Day", "Thursday, November 26***", "Thursday, November 25***",
     "Thursday, November 23*** "],
    ["Christmas Day", "Friday, December 25****", "Friday, December 24 (Christmas Day observed)",
     "Monday, December 25"],
]  # fmt: skip
EARLY = "Each market will close early at 1:00 p.m. (1:15 p.m. for eligible options) on"
NYSE_NOTES = [
    "* Because the holiday falls on Saturday, January 1, 2028, no New Year’s Day holiday "
    "is observed.",
    f"** {EARLY} Monday, July 3, 2028. All times are Eastern Time.",
    f"*** {EARLY} Friday, November 27, 2026, Friday, November 26, 2027, and Friday, "
    "November 24, 2028 (the day after Thanksgiving).",
    f"**** {EARLY} Thursday, December 24, 2026.",
]


def test_nyse_closures_and_early_closes():
    found = ec.parse_nyse(nyse_page(NYSE_ROWS, NYSE_NOTES))
    assert found.years == [2026, 2027, 2028]
    assert len([d for d in found.closed if d.year == 2026]) == 10
    assert len([d for d in found.closed if d.year == 2028]) == 9  # no New Year's observed
    assert found.closed[date(2027, 12, 24)] == "Christmas Day"
    assert date(2028, 1, 1) not in found.closed
    assert found.early == {
        date(2028, 7, 3): time(13, 0),
        date(2026, 11, 27): time(13, 0),
        date(2027, 11, 26): time(13, 0),
        date(2028, 11, 24): time(13, 0),
        date(2026, 12, 24): time(13, 0),
    }


def test_nyse_early_closes_from_html_footnotes():
    # #222: NYSE's page now carries the footnotes as paragraphs, not component JSON
    page = nyse_page(NYSE_ROWS, []) + "".join(
        f'<p data-type="disclaimer" class="mb-6"><span>{note}</span></p>' for note in NYSE_NOTES
    )
    assert ec.parse_nyse(page).early == ec.parse_nyse(nyse_page(NYSE_ROWS, NYSE_NOTES)).early
    assert len(ec.parse_nyse(page).early) == 5


def test_a_weekday_that_does_not_match_its_date_fails_loudly():
    shifted = [row[:] for row in NYSE_ROWS]
    shifted[1][1] = "Friday, January 1"  # 2026-01-01 is a Thursday
    with pytest.raises(ValueError, match="Thursday"):
        ec.parse_nyse(nyse_page(shifted, NYSE_NOTES))
    with pytest.raises(ValueError, match="no holiday table"):
        ec.parse_nyse("<html>maintenance</html>")


def test_checking_new_dates_against_stored_ones():
    today = date(2026, 10, 13)
    stored = {date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9)}
    assert ec.check_dates("FOMC", [date(2026, 10, 28), date(2026, 12, 9)], stored, today) is None
    problem = ec.check_dates("FOMC", [date(2026, 12, 9)], stored, today)
    assert problem == "FOMC: dates stored before are missing now: 2026-10-28"
    weekend = ec.check_dates("CPI", [date(2026, 10, 17)], set(), today)
    assert weekend == "CPI: weekend dates 2026-10-17"


def test_a_date_outside_the_listed_years_is_refused():
    from optionsjourney.core import sessions

    rows = [*NYSE_ROWS, ["Stray", "Monday, March 1, 2027", "—", "Friday, March 2, 2029"]]
    found = ec.parse_nyse(nyse_page(rows, NYSE_NOTES))
    assert date(2029, 3, 2) in found.closed  # a year the header doesn't list
    problem = ec.nyse_problem(found, date(2026, 10, 13), sessions.NYSE_HOLIDAYS,
                              sessions.NYSE_EARLY_CLOSES, lambda d: True)  # fmt: skip
    assert problem is not None and "2029" in problem and "doesn't list" in problem
    early = ec.parse_nyse(nyse_page(NYSE_ROWS, [*NYSE_NOTES,
        "Each market will close early at 1:00 p.m. on Friday, November 23, 2029."]))  # fmt: skip
    problem = ec.nyse_problem(early, date(2026, 10, 13), sessions.NYSE_HOLIDAYS,
                              sessions.NYSE_EARLY_CLOSES, lambda d: True)  # fmt: skip
    assert problem is not None and "2029" in problem


def _event(title, at, impact="medium", key=None, source="forexfactory", **figures):
    return SimpleNamespace(title=title, event_time=at, impact=impact, key=key, source=source,
                           forecast=figures.get("forecast"),
                           previous=figures.get("previous"))  # fmt: skip


def test_a_days_events_as_the_drawers_chips():
    items = ec.day_items([
        _event("Core CPI m/m", "08:30", "high", "CPI", forecast="0.3%", previous="0.3%"),
        _event("CPI m/m", "08:30", "high", "CPI", forecast="0.3%", previous="0.4%"),
        _event("CPI", "08:30", "high", "CPI", source="fred"),  # ForexFactory's rows win
        _event("Treasury Currency Report", None),
        _event("Crude Oil Inventories", "10:30"),
        _event("President Speaks", "10:30", "high"),
    ])  # fmt: skip
    assert [(i["time"], i["label"], i["impact"]) for i in items] == [
        (None, "Treasury Currency Report", "medium"),  # all day first
        ("08:30", "CPI", "high"),
        ("10:30", "President Speaks", "high"),  # high before medium at the same time
        ("10:30", "Crude Oil Inventories", "medium"),
    ]
    assert items[1]["key"] == "CPI" and items[1]["prints"] == [
        {"title": "CPI m/m", "forecast": "0.3%", "previous": "0.4%"},
        {"title": "Core CPI m/m", "forecast": "0.3%", "previous": "0.3%"},
    ]


def test_a_list_day_without_forexfactory_uses_the_feds_row():
    (item,) = ec.day_items([_event("FOMC", "14:00", "high", "FOMC", source="fed")])
    assert (item["time"], item["label"], item["prints"][0]["title"]) == ("14:00", "FOMC", "FOMC")
