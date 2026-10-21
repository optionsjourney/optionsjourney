"""Alembic environment.

The app passes its own connection in (db.migrate). For writing a migration,
`alembic -c src/optionsjourney/db/alembic.ini revision --autogenerate -m "..."`
connects to the database from your settings instead."""

from alembic import context

import optionsjourney.db.models  # noqa: F401 -- registers the tables
from optionsjourney.db import Base, make_engine

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite can't ALTER most things in place
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    from optionsjourney.config import load_settings

    with make_engine(load_settings().database_path).begin() as own_connection:
        _run(own_connection)
