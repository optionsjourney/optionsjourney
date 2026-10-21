"""Stock holdings saved after each close, for the Stocks performance charts (#236)

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("stock_snapshots")
