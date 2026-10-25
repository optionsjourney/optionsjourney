import gzip
import sqlite3
from datetime import UTC, datetime

import pytest

from optionsjourney import __version__
from optionsjourney.app import NewerDataError, UpgradeBackupError, create_app
from optionsjourney.config import Settings
from optionsjourney.db import make_engine, schema_state
from optionsjourney.services import backups
from optionsjourney.services import settings as settings_service
from optionsjourney.services.backups import BackupError


@pytest.fixture
def older_settings(tmp_path, migrate_to_before_head):
    s = Settings(broker_credentials="local", data_dir=tmp_path)
    engine = make_engine(s.database_path)
    migrate_to_before_head(engine)
    # Never the real backup folder in the user's Documents
    _put_setting(engine, "backup_folder", str(tmp_path / "backups"))
    engine.dispose()
    return s


def _put_setting(engine, key, value):
    from optionsjourney.db.models import Setting

    with engine.begin() as c:
        c.execute(
            Setting.__table__.insert().values(key=key, value=value, updated_at=datetime.now(UTC))
        )


def _set_version(s, value):
    engine = make_engine(s.database_path)
    _put_setting(engine, "_app_version", value)
    engine.dispose()


def _state(s):
    engine = make_engine(s.database_path)
    try:
        return schema_state(engine)
    finally:
        engine.dispose()


def _backup_folder(s):
    engine = make_engine(s.database_path)
    from optionsjourney.db import make_session_factory

    try:
        return backups.folder(make_session_factory(engine))
    finally:
        engine.dispose()


def test_an_upgrade_backs_up_first(older_settings):
    _set_version(older_settings, "0.1.9")
    app = create_app(older_settings)
    names = [p.name for p in _backup_folder(older_settings).iterdir()]
    assert len(names) == 1
    assert names[0].endswith(f"-before-{__version__}-from-0.1.9.db.gz")
    assert _state(older_settings) == "current"
    factory = app.extensions["optionsjourney"].session_factory
    assert settings_service.get_app_version(factory) == __version__


def test_no_backup_when_nothing_changes(older_settings):
    create_app(older_settings)
    folder = _backup_folder(older_settings)
    before = sorted(p.name for p in folder.iterdir())
    create_app(older_settings)
    assert sorted(p.name for p in folder.iterdir()) == before


def test_a_new_database_makes_no_backup(settings, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(backups, "backup_before_upgrade", lambda *a: calls.append(a))
    create_app(settings)
    assert calls == []


def test_unknown_from_version(older_settings):
    create_app(older_settings)
    (name,) = [p.name for p in _backup_folder(older_settings).iterdir()]
    assert name.endswith("-from-unknown.db.gz")


def test_the_backup_holds_the_old_data(older_settings):
    create_app(older_settings)
    (path,) = list(_backup_folder(older_settings).iterdir())
    plain = path.with_suffix("")
    with gzip.open(path) as src:
        plain.write_bytes(src.read())
    from alembic.script import ScriptDirectory

    from optionsjourney.db import alembic_config

    scripts = ScriptDirectory.from_config(alembic_config())
    head = scripts.get_current_head()
    with sqlite3.connect(plain) as c:
        backed_up = c.execute("select version_num from alembic_version").fetchone()[0]
    c.close()
    assert backed_up == scripts.get_revision(head).down_revision
    assert _state(older_settings) == "current"


def test_a_failed_backup_stops_the_upgrade(older_settings, monkeypatch):
    def boom(*a, **k):
        raise BackupError("no room")

    monkeypatch.setattr(backups, "backup_before_upgrade", boom)
    with pytest.raises(UpgradeBackupError):
        create_app(older_settings)
    assert _state(older_settings) == "older"


def _unusable(tmp_path, name):
    blocker = tmp_path / f"{name}-is-a-file"
    blocker.write_text("x")
    return blocker / "Backups"


def test_a_gone_backup_folder_backs_up_to_the_default_and_upgrades(older_settings, tmp_path):
    from pathlib import Path

    from optionsjourney.core import settings as core_settings

    engine = make_engine(older_settings.database_path)
    with engine.begin() as c:
        c.exec_driver_sql(
            "UPDATE settings SET value=? WHERE key='backup_folder'",
            (str(_unusable(tmp_path, "usb")),),
        )
    engine.dispose()
    create_app(older_settings)
    default = Path(core_settings.spec("backup_folder").default)  # in tmp (conftest)
    (name,) = [p.name for p in default.iterdir()]
    assert name.endswith(f"-before-{__version__}-from-unknown.db.gz")
    assert _state(older_settings) == "current"


def test_both_backup_folders_unusable_stops_the_upgrade(older_settings, tmp_path, monkeypatch):
    from dataclasses import replace

    from optionsjourney.core import settings as core_settings

    engine = make_engine(older_settings.database_path)
    with engine.begin() as c:
        c.exec_driver_sql(
            "UPDATE settings SET value=? WHERE key='backup_folder'",
            (str(_unusable(tmp_path, "usb")),),
        )
    engine.dispose()
    monkeypatch.setitem(
        core_settings._BY_KEY,
        "backup_folder",
        replace(core_settings.spec("backup_folder"), default=str(_unusable(tmp_path, "documents"))),
    )
    before = older_settings.database_path.read_bytes()
    with pytest.raises(UpgradeBackupError):
        create_app(older_settings)
    assert _state(older_settings) == "older"
    assert older_settings.database_path.read_bytes() == before


def test_newer_data_is_refused_untouched(settings):
    create_app(settings)
    engine = make_engine(settings.database_path)
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE alembic_version SET version_num='ffffffffffff'")
    engine.dispose()
    before = settings.database_path.read_bytes()
    with pytest.raises(NewerDataError):
        create_app(settings)
    assert settings.database_path.read_bytes() == before


def test_brokers_you_are_not_logged_in_to_are_never_asked():
    # #304: no "isn't connected" errors on every refresh for a broker you never connected
    from optionsjourney.app import _logged_in

    class Client:
        def __init__(self, broker):
            self.broker = broker

    class Credentials:
        logged_in = {"tastytrade"}

        def connected(self, broker):
            return broker in self.logged_in

    clients = [Client("tastytrade"), Client("schwab")]
    credentials = Credentials()
    listed = _logged_in(clients, credentials)
    assert [c.broker for c in listed()] == ["tastytrade"]
    credentials.logged_in.add("schwab")  # connecting needs no restart
    assert [c.broker for c in listed()] == ["tastytrade", "schwab"]
    # Gateway credentials have no such check: every client is used
    assert _logged_in(clients, object())() == clients
