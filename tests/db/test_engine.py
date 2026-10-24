import sqlite3
import threading

from sqlalchemy import text

from optionsjourney.db import BUSY_TIMEOUT_MS, make_engine


def _pragma(engine, name):
    with engine.connect() as connection:
        return connection.execute(text(f"PRAGMA {name}")).scalar()


def test_connections_use_wal_and_wait_for_a_busy_database(tmp_path):
    engine = make_engine(tmp_path / "a.db")
    assert _pragma(engine, "journal_mode") == "wal"
    assert _pragma(engine, "busy_timeout") == BUSY_TIMEOUT_MS
    assert _pragma(engine, "foreign_keys") == 1


def test_data_from_an_older_version_switches_to_wal_when_opened(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as old:  # the default rollback journal, as before
        old.execute("create table t(x)")
        old.execute("insert into t values (1)")
    old.close()
    engine = make_engine(path)
    assert _pragma(engine, "journal_mode") == "wal"
    with engine.connect() as connection:
        assert connection.execute(text("select x from t")).scalar() == 1


def test_reading_doesnt_wait_for_a_write_in_progress(tmp_path):
    engine = make_engine(tmp_path / "a.db")
    with engine.begin() as connection:
        connection.execute(text("create table t(x)"))
        connection.execute(text("insert into t values (1)"))
    writer = engine.connect()
    writer.begin()
    writer.execute(text("insert into t values (2)"))  # holds the write lock, uncommitted
    seen = []
    reader = threading.Thread(
        target=lambda: seen.append(_count(engine)), daemon=True
    )
    reader.start()
    reader.join(timeout=2)
    writer.rollback()
    writer.close()
    assert seen == [1]  # answered at once, from before the write


def _count(engine):
    with engine.connect() as connection:
        return connection.execute(text("select count(*) from t")).scalar()
