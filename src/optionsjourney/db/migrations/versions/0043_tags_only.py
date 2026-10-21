"""Tags only (decision 0072, #170): strategies become tags, a bot files under a tag

Every strategy becomes a tag of the same name (an existing tag, any case, is reused),
every trade filed under a strategy carries that tag, each bot files under its
strategy's tag (bots.tag_id), saved dashboards that filtered by a strategy filter by
its tag ("no strategy" becomes "no tags"), and then trade_edits.strategy_id,
bots.strategy_id and the strategies table are dropped.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-18
"""

import json
import re
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

NONE = "none"  # journal.UNCATEGORIZED: "no strategy" before, "no tags" now


def _tag_id(connection, name: str) -> int:
    """The tag with this name (any case), created if missing (as in 0008)."""
    found = connection.execute(
        sa.text("SELECT id FROM tags WHERE lower(name) = lower(:name)"), {"name": name}
    ).scalar()
    if found is None:
        connection.execute(sa.text("INSERT INTO tags (name) VALUES (:name)"), {"name": name})
        found = connection.execute(
            sa.text("SELECT id FROM tags WHERE name = :name"), {"name": name}
        ).scalar()
    return found


def _rewrite_table_sql(connection, table: str, change) -> None:
    """Change a table's CREATE TABLE text in place (SQLite's documented way to change a
    constraint without a table copy: sqlite.org/lang_altertable.html, "otheralter").
    bots can't be copied: with foreign keys on, dropping the old table would cascade
    through bot_runs, bot_legs and the rest. Only constraints that existing rows already
    meet are changed, so nothing on disk needs to."""
    sql = connection.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :t"), {"t": table}
    ).scalar()
    new = change(sql)
    if new == sql:
        raise RuntimeError(f"0043: the {table} table isn't as expected")
    version = connection.execute(sa.text("PRAGMA schema_version")).scalar()
    connection.execute(sa.text("PRAGMA writable_schema = ON"))
    connection.execute(
        sa.text("UPDATE sqlite_master SET sql = :sql WHERE type = 'table' AND name = :t"),
        {"sql": new, "t": table},
    )
    connection.execute(sa.text(f"PRAGMA schema_version = {int(version) + 1}"))
    connection.execute(sa.text("PRAGMA writable_schema = OFF"))


def _without_foreign_key(column: str):
    """Drops a table-level FOREIGN KEY(column) clause (SQLite won't drop a column that
    one names)."""
    pattern = re.compile(
        r",\s*FOREIGN KEY\s*\(\s*" + column + r"\s*\)\s*REFERENCES\s+\w+\s*\(\s*id\s*\)"
        r"(\s+ON DELETE (RESTRICT|CASCADE|SET NULL))?",
        re.IGNORECASE,
    )
    return lambda sql: pattern.sub("", sql, count=1)


def _not_null(column: str):
    """Makes a column added as `column INTEGER REFERENCES ...` NOT NULL (its rows are
    all filled first)."""
    return lambda sql: sql.replace(
        f"{column} INTEGER REFERENCES", f"{column} INTEGER NOT NULL REFERENCES", 1
    )


def _bots_on_tags(sql: str) -> str:
    """bots without its strategy foreign key, and tag_id NOT NULL with a table-level
    foreign key (the form SQLAlchemy reads ON DELETE back from, as the model has it)."""
    sql = _without_foreign_key("strategy_id")(sql)
    sql = sql.replace(
        "tag_id INTEGER REFERENCES tags(id) ON DELETE RESTRICT", "tag_id INTEGER NOT NULL", 1
    )
    return re.sub(
        r"\)\s*$", ", \n\tFOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE RESTRICT\n)", sql
    )


def _dashboards(connection, change) -> None:
    rows = connection.execute(sa.text("SELECT id, filters FROM dashboards")).all()
    for dashboard_id, raw in rows:
        filters = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        changed = change(dict(filters))
        if changed != filters:
            connection.execute(
                sa.text("UPDATE dashboards SET filters = :f WHERE id = :id"),
                {"f": json.dumps(changed), "id": dashboard_id},
            )


def upgrade() -> None:
    connection = op.get_bind()
    tag_of = {
        strategy_id: _tag_id(connection, name)
        for strategy_id, name in connection.execute(sa.text("SELECT id, name FROM strategies"))
    }

    # Every trade filed under a strategy carries its tag
    for key, strategy_id in connection.execute(
        sa.text("SELECT position_key, strategy_id FROM trade_edits WHERE strategy_id IS NOT NULL")
    ).all():
        connection.execute(
            sa.text("INSERT OR IGNORE INTO trade_tags (position_key, tag_id) VALUES (:k, :t)"),
            {"k": key, "t": tag_of[strategy_id]},
        )

    # Each bot files under its strategy's tag. In place, never a table copy (see
    # _rewrite_table_sql); SQLite adds a nullable column with REFERENCES in place.
    op.execute("ALTER TABLE bots ADD COLUMN tag_id INTEGER REFERENCES tags(id) ON DELETE RESTRICT")
    for bot_id, strategy_id in connection.execute(
        sa.text("SELECT id, strategy_id FROM bots")
    ).all():
        connection.execute(
            sa.text("UPDATE bots SET tag_id = :t WHERE id = :id"),
            {"t": tag_of[strategy_id], "id": bot_id},
        )
    _rewrite_table_sql(connection, "bots", _bots_on_tags)
    op.execute("ALTER TABLE bots DROP COLUMN strategy_id")

    # Dashboards: a strategy filter becomes its tag, "no strategy" becomes "no tags"
    def to_tags(filters: dict) -> dict:
        strategies = filters.pop("strategy", None)
        if not strategies:
            return filters
        tags = [str(t) for t in filters.get("tag", ())]
        for value in strategies:
            value = str(value)
            tag = (
                NONE
                if value == NONE
                else (str(tag_of[int(value)]) if value.isdigit() and int(value) in tag_of else None)
            )
            if tag is not None and tag not in tags:
                tags.append(tag)
        if tags:
            filters["tag"] = tags
        return filters

    _dashboards(connection, to_tags)

    with op.batch_alter_table("trade_edits") as batch:  # no table points at trade_edits
        batch.drop_column("strategy_id")
    op.drop_table("strategies")


def downgrade() -> None:
    """Strategies come back from the bots' tags only (decision 0072): trades keep their
    tags and are filed under no strategy."""
    connection = op.get_bind()
    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    strategy_of: dict[int, int] = {}
    for tag_id, name in connection.execute(
        sa.text("SELECT DISTINCT tags.id, tags.name FROM tags JOIN bots ON bots.tag_id = tags.id")
    ).all():
        connection.execute(
            sa.text("INSERT INTO strategies (name, created_at) VALUES (:n, :c)"),
            {"n": name, "c": now},
        )
        strategy_of[tag_id] = connection.execute(
            sa.text("SELECT id FROM strategies WHERE name = :n"), {"n": name}
        ).scalar()

    op.execute(
        "ALTER TABLE bots ADD COLUMN strategy_id INTEGER "
        "REFERENCES strategies(id) ON DELETE RESTRICT"
    )
    for bot_id, tag_id in connection.execute(sa.text("SELECT id, tag_id FROM bots")).all():
        connection.execute(
            sa.text("UPDATE bots SET strategy_id = :s WHERE id = :id"),
            {"s": strategy_of[tag_id], "id": bot_id},
        )
    _rewrite_table_sql(
        connection,
        "bots",
        lambda sql: _not_null("strategy_id")(_without_foreign_key("tag_id")(sql)),
    )
    op.execute("ALTER TABLE bots DROP COLUMN tag_id")

    op.execute(
        "ALTER TABLE trade_edits ADD COLUMN strategy_id INTEGER "
        "REFERENCES strategies(id) ON DELETE SET NULL"
    )

    def no_tags_back(filters: dict) -> dict:
        tags = [str(t) for t in filters.get("tag", ())]
        if NONE not in tags:
            return filters
        tags.remove(NONE)
        filters["strategy"] = [NONE]
        if tags:
            filters["tag"] = tags
        else:
            filters.pop("tag", None)
        return filters

    _dashboards(connection, no_tags_back)
