from datetime import UTC, datetime, timedelta

from optionsjourney.core.backups import (
    backup_name,
    backup_time,
    is_due,
    to_delete,
    upgrade_backup_name,
    upgrade_versions,
    upgrades_to_delete,
)
from optionsjourney.core.market import MARKET_TZ

NOW = datetime(2026, 10, 10, 19, 30, tzinfo=UTC)  # 15:30 New York


def test_names_carry_the_new_york_time_and_sort_by_it():
    assert backup_name(NOW) == "optionsjourney-2026-10-10-1530.db.gz"  # compressed (#13)
    assert backup_time("optionsjourney-2026-10-10-1530.db.gz") == datetime(
        2026, 10, 10, 19, 30, tzinfo=UTC
    )
    assert backup_time("optionsjourney-2026-10-10-1530.db") == datetime(
        2026, 10, 10, 19, 30, tzinfo=UTC
    )
    assert backup_time("holiday-photo.jpg") is None


def test_daily_backups_are_due_on_a_new_day():
    assert is_due(None, "daily", NOW)
    assert not is_due(NOW - timedelta(hours=3), "daily", NOW)
    assert is_due(NOW - timedelta(hours=20), "daily", NOW)  # yesterday in New York


def test_weekly_backups_are_due_after_a_week():
    assert not is_due(NOW - timedelta(days=6), "weekly", NOW)
    assert is_due(NOW - timedelta(days=7), "weekly", NOW)


def test_off_is_never_due():
    assert not is_due(None, "off", NOW)


def test_only_the_newest_30_are_kept_and_other_files_are_left_alone():
    names = [backup_name(NOW - timedelta(days=n)) for n in range(32)] + ["notes.txt"]
    deleted = to_delete(names, keep=30)
    assert deleted == [backup_name(NOW - timedelta(days=31)), backup_name(NOW - timedelta(days=30))]


def test_older_uncompressed_backups_still_count():
    assert backup_time("optionsjourney-2026-10-09-1530.db") is not None


def test_upgrade_backup_names_round_trip():
    now = datetime(2026, 10, 21, 15, 30, tzinfo=MARKET_TZ)
    name = upgrade_backup_name(now, "0.2.0", "0.2.1")
    assert name == "optionsjourney-2026-10-21-1530-before-0.2.1-from-0.2.0.db.gz"
    assert upgrade_versions(name) == ("0.2.0", "0.2.1")
    assert backup_time(name) == now
    assert upgrade_versions("optionsjourney-2026-10-21-1530.db.gz") is None


def test_upgrade_backup_names_accept_unknown_versions():
    name = upgrade_backup_name(NOW, "unknown", "0+unknown")
    assert upgrade_versions(name) == ("unknown", "0+unknown")


def test_the_usual_cleanup_keeps_pre_upgrade_backups_and_they_keep_3():
    plain = [f"optionsjourney-2026-10-{d:02d}-1530.db.gz" for d in range(1, 6)]
    upgrades = [
        f"optionsjourney-2026-10-{d:02d}-1600-before-0.2.{d}-from-0.2.{d - 1}.db.gz"
        for d in range(1, 6)
    ]
    assert to_delete(plain + upgrades, keep=2) == plain[:3]
    assert upgrades_to_delete(plain + upgrades) == upgrades[:2]
