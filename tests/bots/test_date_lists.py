from datetime import date

import pytest

from optionsjourney.bots import date_lists
from optionsjourney.core import sessions


@pytest.fixture(autouse=True)
def table_only():
    sessions.use_fetched_calendar([], {})
    yield
    sessions.use_fetched_calendar([], {})


def test_end_of_month_steps_back_over_weekends_and_holidays():
    assert date_lists.eom_day(2026, 10) == date(2026, 10, 30)  # the 31st is a Saturday
    assert date_lists.eom_day(2026, 12) == date(2026, 12, 31)
    # 2027-12-31 is a Friday; 2027-12-24 is a holiday, but the 31st trades
    assert date_lists.eom_day(2027, 12) == date(2027, 12, 31)
    sessions.use_fetched_calendar([date(2026, 12, 31), date(2026, 1, 1)], {})
    assert date_lists.eom_day(2026, 12) == date(2026, 12, 30)


def test_end_of_month_membership_and_its_reach():
    assert date_lists.eom_member(date(2026, 10, 30)) is True
    assert date_lists.eom_member(date(2026, 10, 29)) is False
    assert date_lists.eom_member(date(2029, 1, 31)) is None  # past the calendar
    upcoming = date_lists.eom_upcoming(date(2027, 11, 1))
    assert upcoming == [date(2027, 11, 30), date(2027, 12, 31)]


def test_forexfactorys_week_runs_sunday_to_saturday():
    assert date_lists.ff_week(date(2026, 10, 13)) == (date(2026, 10, 11), date(2026, 10, 17))
    assert date_lists.ff_week(date(2026, 10, 11)) == (date(2026, 10, 11), date(2026, 10, 17))
    assert date_lists.ff_week(date(2026, 10, 17)) == (date(2026, 10, 11), date(2026, 10, 17))


def test_a_market_holiday_is_a_weekday_with_no_session():
    assert date_lists.holiday_member(date(2026, 11, 26)) is True  # Thanksgiving
    assert date_lists.holiday_member(date(2026, 11, 27)) is False  # a half day trades
    assert date_lists.holiday_member(date(2026, 11, 28)) is False  # a Saturday isn't one
    assert date_lists.holiday_member(date(2029, 1, 1)) is None  # past the calendar
    assert date_lists.holiday_upcoming(date(2027, 11, 1)) == [date(2027, 11, 25),
                                                              date(2027, 12, 24)]  # fmt: skip


def test_market_holiday_is_a_before_expiration_blackout_list_only():
    assert "HOLIDAY" in date_lists.DATE_SETS and "HOLIDAY" in date_lists.COMPUTED_SETS
    assert date_lists.SPECIFIC_SETS == ("FOMC", "CPI", "EOM")
    assert date_lists.ENTRY_DAY_SETS == ("FOMC", "CPI", "EOM")
    assert date_lists.LABELS["HOLIDAY"] == "Market holidays"
    assert date_lists.short_label("HOLIDAY") == "Market holiday"
    assert date_lists.short_label("FOMC") == "FOMC"
