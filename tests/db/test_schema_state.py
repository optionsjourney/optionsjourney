from alembic.script import ScriptDirectory
from sqlalchemy import text

from optionsjourney.db import alembic_config, make_engine, migrate, schema_state


def test_schema_state(tmp_path):
    engine = make_engine(tmp_path / "a.db")
    assert schema_state(engine) == "empty"
    migrate(engine)
    assert schema_state(engine) == "current"
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='ffffffffffff'"))
    assert schema_state(engine) == "newer"


def test_older_data(tmp_path, migrate_to_before_head):
    engine = make_engine(tmp_path / "b.db")
    migrate_to_before_head(engine)
    assert schema_state(engine) == "older"
    migrate(engine)
    assert schema_state(engine) == "current"
    assert ScriptDirectory.from_config(alembic_config()).get_current_head()
