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
