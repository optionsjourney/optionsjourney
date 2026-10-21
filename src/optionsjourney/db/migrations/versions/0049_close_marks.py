"""Each option's last real two-sided mark, for pricing it after hours (#241)

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "close_marks",
        sa.Column("symbol", sa.String(80), primary_key=True),
        sa.Column("mark", optionsjourney.db.Money(), nullable=False),
        sa.Column("saved_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("close_marks")
