"""Drop the daily stock save: the Stocks chart is worked back from today's holdings (#286)

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("stock_snapshots")


def downgrade() -> None:
    op.create_table(
        "stock_snapshots",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("account", sa.String(64), primary_key=True),
        sa.Column("ticker", sa.String(32), primary_key=True),
        sa.Column("quantity", optionsjourney.db.Money(), nullable=False),
        sa.Column("cost_basis", optionsjourney.db.Money(), nullable=False),
        sa.Column("close", optionsjourney.db.Money(), nullable=True),
        sa.Column("taken_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
