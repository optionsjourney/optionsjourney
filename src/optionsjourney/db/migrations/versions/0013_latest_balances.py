"""Latest balances: each account's balance as last read, for the Accounts page

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "latest_balances",
        sa.Column("broker", sa.String(20), primary_key=True),
        sa.Column("account_number", sa.String(40), primary_key=True),
        sa.Column("nickname", sa.String(100), nullable=True),
        sa.Column("account_type", sa.String(50), nullable=True),
        sa.Column("net_liquidating_value", optionsjourney.db.Money(), nullable=False),
        sa.Column("cash", optionsjourney.db.Money(), nullable=False),
        sa.Column("buying_power", optionsjourney.db.Money(), nullable=False),
        sa.Column("read_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("latest_balances")
