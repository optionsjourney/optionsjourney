from datetime import UTC, datetime, timedelta

from optionsjourney.core import auto_sync
from optionsjourney.core.settings import spec

# Monday 19 October 2026: 10:00 and 18:00 New York (EDT, UTC-4); Saturday the 17th, noon.
OPEN = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
EVENING = datetime(2026, 10, 19, 22, 0, tzinfo=UTC)
WEEKEND = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)


def test_the_defaults_are_every_minute_open_and_hourly_otherwise():
    assert spec("sync_market_hours").default == "1"
    assert spec("sync_other_times").default == "60"


def test_interval_follows_the_market_session():
    assert auto_sync.interval(OPEN, "1", "60") == timedelta(minutes=1)
    assert auto_sync.interval(EVENING, "1", "60") == timedelta(minutes=60)
    assert auto_sync.interval(WEEKEND, "5", "15") == timedelta(minutes=15)


def test_off_and_unreadable_choices_never_sync():
    assert auto_sync.interval(OPEN, "off", "60") is None
    assert auto_sync.interval(EVENING, "1", "off") is None
    assert auto_sync.interval(OPEN, "soon", "60") is None


def test_due():
    minute = timedelta(minutes=1)
    assert auto_sync.due(None, OPEN, minute)  # none yet: at once
    assert not auto_sync.due(OPEN, OPEN + timedelta(seconds=30), minute)
    assert auto_sync.due(OPEN, OPEN + minute, minute)
    assert not auto_sync.due(None, OPEN, None)  # off
