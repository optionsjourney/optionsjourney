"""P/L Day: each trade's total P/L after a day's close

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pl_snapshots",
        sa.Column("position_key", sa.String(160), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("total_pl", optionsjourney.db.Money(), nullable=False),
        sa.Column("taken_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pl_snapshots")
