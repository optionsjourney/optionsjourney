from datetime import UTC, datetime, timedelta

import pytest

from optionsjourney.brokers import guard
from optionsjourney.brokers.credentials import AccessToken
from optionsjourney.config import Settings


@pytest.fixture(autouse=True)
def _reset_guard():
    """The guard patches httpx process-wide; don't let it leak between tests."""
    yield
    guard._uninstall_for_tests()


@pytest.fixture(autouse=True)
def _forget_learned_ticks():
    """Stocks' learned increments (trading/ticks.learn) are process-wide too."""
    from optionsjourney.trading import ticks

    yield
    ticks.forget_learned()


@pytest.fixture(autouse=True)
def _forget_tick_failures():
    """A failed tick fetch backs off for minutes, process-wide (services/orders)."""
    from optionsjourney.services import orders

    orders.forget_tick_failures()
    yield
    orders.forget_tick_failures()


@pytest.fixture(autouse=True)
def _history_from_the_start(request, monkeypatch):
    """Tests sync sample trades from any date; history starting on the first
    sync's day (decision 0064) is tested in test_sync alone."""
    if request.module.__name__.endswith("test_sync"):
        return
    from datetime import date  # noqa: PLC0415

    from optionsjourney.services import sync  # noqa: PLC0415

    monkeypatch.setattr(sync, "history_starts", lambda session, now: date.min)


@pytest.fixture
def settings(tmp_path):
    return Settings(broker_credentials="local", data_dir=tmp_path)


@pytest.fixture
def gateway_settings(tmp_path):
    return Settings(
        broker_credentials="gateway",
        data_dir=tmp_path,
        gateway_url="http://tracker2.test:8800",
        gateway_secret="test-secret",
    )


class FakeCredentials:
    def __init__(self, token="tt-token", is_test=False):
        self.token = AccessToken(token, datetime.now(UTC) + timedelta(hours=1), is_test)

    def tastytrade_token(self):
        return self.token

    def schwab_token(self):
        return self.token


@pytest.fixture
def make_credentials():
    return FakeCredentials


@pytest.fixture
def fake_credentials():
    return FakeCredentials()


class _MemoryKeyring:
    """The system keychain, for tests: in memory, so tests never read or
    write the real one."""

    def __init__(self):
        self.items = {}

    def get_password(self, service, user):
        return self.items.get((service, user))

    def set_password(self, service, user, value):
        self.items[(service, user)] = value

    def delete_password(self, service, user):
        self.items.pop((service, user), None)


@pytest.fixture(autouse=True)
def _no_real_keychain(monkeypatch):
    from optionsjourney.brokers.credentials import store

    keychain = _MemoryKeyring()
    monkeypatch.setattr(store, "default_backend", lambda: keychain)
    return keychain


@pytest.fixture(autouse=True)
def _forget_pl_day_closes():
    """P/L Day's previous closes are cached process-wide for the day."""
    from optionsjourney.services import pl_day

    pl_day.forget_closes()
    yield
    pl_day.forget_closes()


@pytest.fixture
def migrate_to_before_head():
    """Migrate a database only to the revision before the newest, so it is
    'older' data an upgrade has something to do to."""

    def _migrate(engine):
        from alembic import command
        from alembic.script import ScriptDirectory

        from optionsjourney.db import alembic_config

        config = alembic_config()
        head = ScriptDirectory.from_config(config).get_current_head()
        previous = ScriptDirectory.from_config(config).get_revision(head).down_revision
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, previous)

    return _migrate


@pytest.fixture(autouse=True)
def _backup_folder_in_tmp(tmp_path_factory, monkeypatch):
    """The backup folder's default is in the user's real Documents; no test may
    write a backup there (create_app backs up before an upgrade)."""
    from dataclasses import replace

    from optionsjourney.core import settings as core_settings

    folder = tmp_path_factory.mktemp("default-backups") / "OptionsJourney Backups"
    monkeypatch.setitem(
        core_settings._BY_KEY,
        "backup_folder",
        replace(core_settings.spec("backup_folder"), default=str(folder)),
    )
