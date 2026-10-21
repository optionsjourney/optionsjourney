"""strategy and stock tags: tag trades with their strategy, add a Stocks tag

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _tag_id(connection, name: str) -> int:
    """The tag with this name (any case), created if missing."""
    found = connection.execute(
        sa.text("SELECT id FROM tags WHERE lower(name) = lower(:name)"), {"name": name}
    ).scalar()
    if found is None:
        connection.execute(sa.text("INSERT INTO tags (name) VALUES (:name)"), {"name": name})
        found = connection.execute(
            sa.text("SELECT id FROM tags WHERE name = :name"), {"name": name}
        ).scalar()
    return found


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT trade_edits.position_key, strategies.name FROM trade_edits "
            "JOIN strategies ON strategies.id = trade_edits.strategy_id"
        )
    ).all()
    for key, strategy in rows:
        tag_id = _tag_id(connection, strategy)
        connection.execute(
            sa.text("INSERT OR IGNORE INTO trade_tags (position_key, tag_id) VALUES (:key, :tag)"),
            {"key": key, "tag": tag_id},
        )
    _tag_id(connection, "Stocks")


def downgrade() -> None:
    pass  # tags the user may have kept or changed since; nothing to undo safely
