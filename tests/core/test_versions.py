from datetime import datetime
from zoneinfo import ZoneInfo

from optionsjourney.core.versions import is_newer, market_open, parse_tag

NY = ZoneInfo("America/New_York")


def test_parse_tag():
    assert parse_tag("v0.1.10") == (0, 1, 10)
    assert parse_tag("0.2.0") == (0, 2, 0)
    for bad in ("v0.2.0-beta", "latest", "", "v1.2"):
        assert parse_tag(bad) is None


def test_is_newer_compares_numbers_not_text():
    assert is_newer("v0.1.10", "0.1.9")
    assert not is_newer("v0.1.2", "0.1.2")
    assert not is_newer("v0.1.1", "0.1.2")
    assert not is_newer("v0.2.0-beta", "0.1.0")  # not a plain version: ignored
    assert not is_newer("v0.2.0", "0+unknown")  # running copy never installed: no offer


def test_market_open_is_weekdays_930_to_1600_new_york():
    assert market_open(datetime(2026, 10, 21, 9, 30, tzinfo=NY))
    assert market_open(datetime(2026, 10, 21, 15, 59, tzinfo=NY))
    assert not market_open(datetime(2026, 10, 21, 9, 29, tzinfo=NY))
    assert not market_open(datetime(2026, 10, 21, 16, 0, tzinfo=NY))
    assert not market_open(datetime(2026, 10, 24, 11, 0, tzinfo=NY))  # Saturday


def test_market_open_converts_other_time_zones():
    utc = ZoneInfo("UTC")
    assert market_open(datetime(2026, 10, 21, 14, 0, tzinfo=utc))  # 10:00 in New York
    assert not market_open(datetime(2026, 10, 21, 9, 30, tzinfo=utc))  # 05:30 in New York
