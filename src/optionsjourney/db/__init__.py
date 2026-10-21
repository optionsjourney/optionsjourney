"""The local SQLite database.

Plain SQLAlchemy rather than Flask-SQLAlchemy, so bots and background jobs can
use the database without a Flask app context. The schema is managed by Alembic
migrations (db/migrations), applied at startup."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import DateTime, Engine, String, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator

MIGRATIONS = Path(__file__).parent / "migrations"


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
    def _foreign_keys_on(connection, _record):
        # SQLite ignores foreign keys unless asked, per connection.
        connection.execute("PRAGMA foreign_keys = ON")

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


def is_healthy(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 -- health check reports, never raises
        return False
