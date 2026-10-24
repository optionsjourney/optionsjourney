"""Auto-match: trades a sync never matches to a bot on its own again (#276)

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_auto_skips",
        sa.Column("position_key", sa.String(length=160), nullable=False),
        sa.Column("skipped_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("position_key"),
    )


def downgrade() -> None:
    op.drop_table("bot_auto_skips")
