import pytest

from optionsjourney.core.settings import SettingError, spec
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import settings


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    return make_session_factory(engine)


def test_defaults_until_saved(session_factory):
    assert settings.get(session_factory, "backup_frequency") == "daily"
    assert settings.get(session_factory, "backup_folder") == spec("backup_folder").default


def test_saving_a_section(session_factory):
    settings.save_section(
        session_factory, "backups", {"backup_folder": "/tmp/oj", "backup_frequency": "weekly"}
    )
    assert settings.get(session_factory, "backup_folder") == "/tmp/oj"
    assert settings.get(session_factory, "backup_frequency") == "weekly"


def test_a_refused_section_saves_nothing(session_factory):
    with pytest.raises(SettingError):
        settings.save_section(
            session_factory, "backups", {"backup_folder": "/tmp/oj", "backup_frequency": "hourly"}
        )
    assert settings.get(session_factory, "backup_folder") == spec("backup_folder").default


def _settings(tmp_path, **env):
    from optionsjourney.config import load_settings

    return load_settings({"OPTIONSJOURNEY_DATA_DIR": str(tmp_path), **env})


def test_the_app_answers_only_on_this_computer_by_default(session_factory, tmp_path):
    assert settings.get(session_factory, "listen_on_network") == "off"
    assert settings.bind_host(_settings(tmp_path), session_factory) == "127.0.0.1"


def test_open_from_other_devices_listens_on_the_network(session_factory, tmp_path):
    settings.save_section(
        session_factory, "computer", {"keep_awake": "on", "listen_on_network": "on"}
    )
    assert settings.bind_host(_settings(tmp_path), session_factory) == "0.0.0.0"


def test_the_env_setting_still_opens_it(session_factory, tmp_path):
    env = _settings(tmp_path, LISTEN_ON_NETWORK="yes")
    assert settings.bind_host(env, session_factory) == "0.0.0.0"


def test_demo_mode_ignores_the_switch(session_factory, tmp_path):
    settings.save_section(
        session_factory, "computer", {"keep_awake": "on", "listen_on_network": "on"}
    )
    demo = _settings(tmp_path, BROKER_CREDENTIALS="demo")
    assert settings.bind_host(demo, session_factory) == "127.0.0.1"
