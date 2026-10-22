"""Reading and saving what's set on the Settings page. The settings
themselves, their defaults and checks, are in core/settings.py."""

from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from optionsjourney.core.settings import SECTIONS, clean_section, spec
from optionsjourney.db.models import Setting


def get(session_factory: sessionmaker, key: str) -> str:
    """The saved value, or the default."""
    with session_factory() as session:
        row = session.get(Setting, key)
    return row.value if row is not None else spec(key).default


# Internal key in the settings table; not shown on the Settings page.
_APP_VERSION = "_app_version"


def get_app_version(session_factory: sessionmaker) -> str | None:
    """The app version that last started on this data; None if never recorded."""
    with session_factory() as session:
        row = session.get(Setting, _APP_VERSION)
    return row.value if row is not None else None


def set_app_version(session_factory: sessionmaker, version: str) -> None:
    with session_factory() as session:
        row = session.get(Setting, _APP_VERSION) or Setting(key=_APP_VERSION)
        row.value = version
        row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()


def get_all(session_factory: sessionmaker) -> dict[str, str]:
    return {s.key: get(session_factory, s.key) for section in SECTIONS for s in section.settings}


def save_section(session_factory: sessionmaker, section_key: str, typed) -> None:
    """Check every value in the section and save them together; nothing is
    saved if any is refused (SettingError)."""
    values = clean_section(section_key, typed)
    now = datetime.now(UTC)
    with session_factory() as session:
        for key, value in values.items():
            row = session.get(Setting, key) or Setting(key=key)
            row.value = value
            row.updated_at = now
            session.add(row)
        session.commit()
