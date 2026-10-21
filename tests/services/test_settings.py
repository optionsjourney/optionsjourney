import pytest

from optionsjourney.core.settings import DEFAULT_BACKUP_FOLDER, SettingError
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import settings


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    return make_session_factory(engine)


def test_defaults_until_saved(session_factory):
    assert settings.get(session_factory, "backup_frequency") == "daily"
    assert settings.get(session_factory, "backup_folder") == DEFAULT_BACKUP_FOLDER


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
    assert settings.get(session_factory, "backup_folder") == DEFAULT_BACKUP_FOLDER
