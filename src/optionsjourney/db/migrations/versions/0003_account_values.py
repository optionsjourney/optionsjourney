"""account values: each account's value at the end of each day

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_values",
        sa.Column("broker", sa.String(20), primary_key=True),
        sa.Column("account_number", sa.String(40), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("net_liquidating_value", optionsjourney.db.Money(), nullable=False),
        sa.Column("cash", optionsjourney.db.Money(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("recorded_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("account_values")
