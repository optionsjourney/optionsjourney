import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from optionsjourney.core.backups import backup_name
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import Tag
from optionsjourney.services import backups, settings

NOW = datetime(2026, 10, 10, 19, 30, tzinfo=UTC)


@pytest.fixture
def app_db(tmp_path):
    path = tmp_path / "data" / "optionsjourney.db"
    engine = make_engine(path)
    migrate(engine)
    factory = make_session_factory(engine)
    folder = tmp_path / "backups"
    settings.save_section(
        factory, "backups", {"backup_folder": str(folder), "backup_frequency": "daily"}
    )
    return path, factory, folder


def _tags(path):
    if str(path).endswith(".gz"):  # a compressed backup: unzip a copy to look inside
        import gzip
        import shutil

        plain = path.with_suffix("")
        with gzip.open(path, "rb") as src, open(plain, "wb") as dst:
            shutil.copyfileobj(src, dst)
        path = plain
    with sqlite3.connect(path) as connection:
        return sorted(name for (name,) in connection.execute("select name from tags"))


def test_backing_up_makes_a_copy_in_the_folder(app_db):
    path, factory, folder = app_db
    made = backups.backup_now(path, factory, now=NOW)
    assert made == folder / backup_name(NOW)
    assert _tags(made) == _tags(path)  # a working copy of the database


def test_old_backups_go_beyond_30(app_db):
    path, factory, folder = app_db
    for n in range(32):
        backups.backup_now(path, factory, now=NOW - timedelta(days=n))
    (folder / "my notes.txt").write_text("keep me")
    backups.backup_now(path, factory, now=NOW + timedelta(days=1))
    names = sorted(p.name for p in folder.iterdir())
    assert len([n for n in names if n.endswith(".db.gz")]) == 30
    assert "my notes.txt" in names


def test_runs_only_when_due(app_db):
    path, factory, folder = app_db
    assert backups.run_if_due(path, factory, now=NOW) is not None
    assert backups.run_if_due(path, factory, now=NOW + timedelta(hours=1)) is None
    assert backups.run_if_due(path, factory, now=NOW + timedelta(days=1)) is not None


def test_a_folder_that_cant_be_written_is_a_problem_not_a_crash(app_db, tmp_path):
    path, factory, _ = app_db
    blocker = tmp_path / "a file"
    blocker.write_text("not a folder")
    settings.save_section(
        factory, "backups", {"backup_folder": str(blocker / "inside"), "backup_frequency": "daily"}
    )
    with pytest.raises(backups.BackupError, match="can't"):
        backups.backup_now(path, factory, now=NOW)


def test_restoring_brings_back_the_backup_and_keeps_a_copy_of_now(app_db):
    path, factory, folder = app_db
    made = backups.backup_now(path, factory, now=NOW)
    with factory() as session:
        session.add(Tag(name="Added after the backup"))
        session.commit()
    backups.restore(path, factory, made.name, now=NOW + timedelta(hours=1))
    assert "Added after the backup" not in _tags(path)
    newest = max(folder.iterdir())
    assert "Added after the backup" in _tags(newest)  # the state before restoring was saved


def test_backup_and_restore_include_changes_still_in_the_wal_file(app_db):
    path, factory, _ = app_db
    with factory() as session:
        session.add(Tag(name="Only in the WAL"))
        session.commit()
    assert path.with_name(path.name + "-wal").exists()  # not yet folded into the file
    made = backups.backup_now(path, factory, now=NOW)
    assert "Only in the WAL" in _tags(made)
    with factory() as session:
        session.add(Tag(name="After the backup"))
        session.commit()
        backups.restore(path, factory, made.name, now=NOW + timedelta(hours=1))
        # the app's open connection sees the restored data, not the old WAL
        names = {tag.name for tag in session.query(Tag)}
    assert "Only in the WAL" in names
    assert "After the backup" not in names


def test_only_backups_in_the_folder_can_be_restored(app_db):
    path, factory, _ = app_db
    with pytest.raises(backups.BackupError):
        backups.restore(path, factory, "../../etc/passwd", now=NOW)


def test_backups_are_compressed(app_db):
    path, factory, _ = app_db
    made = backups.backup_now(path, factory, now=NOW)
    # the database's size, not the file's: recent pages may still be in the -wal file
    with sqlite3.connect(path) as connection:
        pages = connection.execute("pragma page_count").fetchone()[0]
        page_size = connection.execute("pragma page_size").fetchone()[0]
    connection.close()
    assert made.name.endswith(".db.gz") and made.stat().st_size < pages * page_size


def test_how_many_to_keep_is_a_setting(app_db):
    path, factory, folder = app_db
    settings.save_section(
        factory,
        "backups",
        {"backup_folder": str(folder), "backup_frequency": "daily", "backup_keep": "7"},
    )
    for n in range(9):
        backups.backup_now(path, factory, now=NOW - timedelta(days=n))
    assert len(list(folder.glob("*.gz"))) == 7


def test_an_old_uncompressed_backup_can_be_restored(app_db):
    path, factory, folder = app_db
    folder.mkdir(parents=True, exist_ok=True)
    old = folder / "optionsjourney-2026-01-01-1200.db"
    with sqlite3.connect(path) as src, sqlite3.connect(old) as dst:
        src.backup(dst)
    backups.restore(path, factory, old.name, now=NOW)
    assert _tags(path) == _tags(old)


def test_choose_folder_asks_the_computer(monkeypatch):
    import subprocess

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="/Users/me/iCloud/Backups/\n", stderr="")

    monkeypatch.setattr(backups.subprocess, "run", fake_run)
    monkeypatch.setattr(backups.sys, "platform", "darwin")
    assert backups.choose_folder() == "/Users/me/iCloud/Backups"
    assert calls[0][0] == "osascript"

    monkeypatch.setattr(
        backups.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 1, stdout="", stderr="User canceled"),
    )
    assert backups.choose_folder() is None


def test_backup_before_upgrade_is_named_for_the_upgrade_and_keeps_three(app_db):
    path, factory, folder = app_db
    for minute in range(5):
        made = NOW + timedelta(minutes=minute)
        backups.backup_before_upgrade(path, factory, "0.1.9", "0.2.0", made)
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 3
    assert all(n.endswith("-before-0.2.0-from-0.1.9.db.gz") for n in names)


def test_restore_point_is_the_newest_backup_from_before_an_upgrade_of_this_version(app_db):
    path, factory, folder = app_db
    backups.backup_before_upgrade(path, factory, "0.2.0", "0.2.1", NOW)
    backups.backup_before_upgrade(path, factory, "0.2.0", "0.3.0", NOW + timedelta(days=2))
    backups.backup_before_upgrade(path, factory, "0.2.1", "0.3.0", NOW + timedelta(days=3))
    backups.backup_before_upgrade(path, factory, "unknown", "0.2.0", NOW + timedelta(days=4))
    backups.backup_now(path, factory, NOW + timedelta(days=5))
    point = backups.restore_point(factory, running="0.2.0")
    assert point is not None
    assert point.name.endswith("-before-0.3.0-from-0.2.0.db.gz")
    assert backups.restore_point(factory, running="0.1.5") is None


def test_restoring_before_an_upgrade_brings_back_that_backup(app_db):
    path, factory, folder = app_db
    backups.backup_before_upgrade(path, factory, "0.2.0", "0.3.0", NOW)
    with factory() as session:
        session.add(Tag(name="Added by the newer version"))
        session.commit()
    backups.restore_before_upgrade(path, factory, "0.2.0", NOW + timedelta(hours=1))
    assert "Added by the newer version" not in _tags(path)


def test_restoring_before_an_upgrade_without_a_backup_is_a_problem(app_db):
    path, factory, _ = app_db
    with pytest.raises(backups.BackupError, match="No backup from before the upgrade"):
        backups.restore_before_upgrade(path, factory, "0.2.0", NOW)


def _unusable(tmp_path, name):
    """A folder that can't be made: its parent is a file (like a drive that's gone)."""
    blocker = tmp_path / f"{name}-is-a-file"
    blocker.write_text("x")
    return blocker / "Backups"


def _default_folder():
    from pathlib import Path

    from optionsjourney.core import settings as core_settings

    return Path(core_settings.spec("backup_folder").default)  # in tmp (conftest)


def test_before_an_upgrade_a_gone_backup_folder_falls_back_to_the_default(app_db, tmp_path, caplog):
    path, factory, _ = app_db
    chosen = _unusable(tmp_path, "usb")
    settings.save_section(factory, "backups", {"backup_folder": str(chosen)})
    target = backups.backup_before_upgrade(path, factory, "0.2.0", "0.3.0", NOW)
    assert target.parent == _default_folder()
    assert target.name.endswith("-before-0.3.0-from-0.2.0.db.gz")
    assert str(chosen) in caplog.text and str(_default_folder()) in caplog.text


def test_before_an_upgrade_both_folders_unusable_is_a_problem(app_db, tmp_path, monkeypatch):
    from dataclasses import replace

    from optionsjourney.core import settings as core_settings

    path, factory, _ = app_db
    chosen, default = _unusable(tmp_path, "usb"), _unusable(tmp_path, "documents")
    settings.save_section(factory, "backups", {"backup_folder": str(chosen)})
    monkeypatch.setitem(
        core_settings._BY_KEY,
        "backup_folder",
        replace(core_settings.spec("backup_folder"), default=str(default)),
    )
    with pytest.raises(backups.BackupError) as raised:
        backups.backup_before_upgrade(path, factory, "0.2.0", "0.3.0", NOW)
    assert str(chosen) in str(raised.value) and str(default) in str(raised.value)


def test_the_backup_in_the_default_folder_is_the_restore_point(app_db, tmp_path):
    path, factory, folder = app_db
    settings.save_section(factory, "backups", {"backup_folder": str(_unusable(tmp_path, "usb"))})
    backups.backup_before_upgrade(path, factory, "0.2.0", "0.3.0", NOW)
    with factory() as session:
        session.add(Tag(name="Added by the newer version"))
        session.commit()
    settings.save_section(factory, "backups", {"backup_folder": str(folder)})  # drive back
    point = backups.restore_point(factory, running="0.2.0")
    assert point is not None and point.name.endswith("-before-0.3.0-from-0.2.0.db.gz")
    backups.restore_before_upgrade(path, factory, "0.2.0", NOW + timedelta(hours=1))
    assert "Added by the newer version" not in _tags(path)
