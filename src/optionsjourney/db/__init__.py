"""The local SQLite database.

Plain SQLAlchemy rather than Flask-SQLAlchemy, so bots and background jobs can
use the database without a Flask app context. The schema is managed by Alembic
migrations (db/migrations), applied at startup."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import DateTime, Engine, String, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator

MIGRATIONS = Path(__file__).parent / "migrations"
BUSY_TIMEOUT_MS = 10_000  # how long a write waits for another to finish


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes. SQLite has no time zones, so store naive
    UTC and attach UTC on the way out."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime needs a timezone-aware datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class Money(TypeDecorator):
    """Exact decimal amounts. SQLite has no decimal type and would round
    through floats, so store the number as text."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect):
        return None if value is None else str(Decimal(value))

    def process_result_value(self, value: str | None, dialect):
        return None if value is None else Decimal(value)


def make_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _connection_settings(connection, _record):
        # SQLite ignores foreign keys unless asked, per connection.
        connection.execute("PRAGMA foreign_keys = ON")
        # Decision 0112: reads never wait on a write, and a second writer waits
        # its turn instead of failing with "database is locked". WAL is kept in
        # the file, so older data switches the first time it's opened.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def migrate(engine: Engine) -> None:
    """Bring the database up to the latest schema."""
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def schema_state(engine: Engine) -> Literal["empty", "current", "older", "newer"]:
    """Where the data stands against this version's migrations: "empty" (a new
    file), "current", "older" (needs upgrading) or "newer" (a newer version of
    the app upgraded it, so this one can't read it safely)."""
    scripts = ScriptDirectory.from_config(alembic_config())
    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    if revision is None:
        return "empty"
    if revision == scripts.get_current_head():
        return "current"
    try:
        scripts.get_revision(revision)
    except Exception:  # noqa: BLE001 -- Alembic raises its own error for an unknown revision
        return "newer"
    return "older"


def is_healthy(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 -- health check reports, never raises
        return False
