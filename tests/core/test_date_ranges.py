from datetime import date

import pytest

from optionsjourney.core import date_ranges

TODAY = date(2026, 10, 23)


@pytest.mark.parametrize(
    ("key", "start", "end"),
    [
        ("yesterday", date(2026, 10, 22), date(2026, 10, 22)),
        ("7d", date(2026, 10, 17), TODAY),
        ("14d", date(2026, 10, 10), TODAY),
        ("30d", date(2026, 9, 24), TODAY),
        ("month", date(2026, 10, 1), TODAY),
        ("last_month", date(2026, 9, 1), date(2026, 9, 30)),
        ("ytd", date(2026, 1, 1), TODAY),
    ],
)
def test_each_preset_covers_tracker2s_days(key, start, end):
    """#284: as Tracker2's daterangepicker ranges (Last 30 Days: 2026-09-24 to 10-23)."""
    assert date_ranges.window(key, TODAY) == (start, end)


def test_last_month_in_january_is_last_december():
    december = (date(2026, 12, 1), date(2026, 12, 31))
    assert date_ranges.window("last_month", date(2027, 1, 15)) == december
