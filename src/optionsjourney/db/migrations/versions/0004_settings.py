"""settings: values saved on the Settings page

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
