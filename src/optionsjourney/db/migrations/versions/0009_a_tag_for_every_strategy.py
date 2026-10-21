"""a tag for every strategy (an existing tag of the same name is kept)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO tags (name) SELECT name FROM strategies "
            "WHERE lower(name) NOT IN (SELECT lower(name) FROM tags)"
        )
    )


def downgrade() -> None:
    pass  # the tags may be on trades by now
