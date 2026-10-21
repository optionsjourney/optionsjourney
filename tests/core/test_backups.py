from datetime import UTC, datetime, timedelta

from optionsjourney.core.backups import backup_name, backup_time, is_due, to_delete

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
