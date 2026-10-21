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


def test_only_backups_in_the_folder_can_be_restored(app_db):
    path, factory, _ = app_db
    with pytest.raises(backups.BackupError):
        backups.restore(path, factory, "../../etc/passwd", now=NOW)


def test_backups_are_compressed(app_db):
    path, factory, _ = app_db
    made = backups.backup_now(path, factory, now=NOW)
    assert made.name.endswith(".db.gz") and made.stat().st_size < path.stat().st_size


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
