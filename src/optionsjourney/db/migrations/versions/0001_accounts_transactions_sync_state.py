"""accounts, broker transactions and sync state

Revision ID: 0001
Revises:
Create Date: 2026-10-09
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("nickname", sa.String(100), nullable=True),
        sa.Column("account_type", sa.String(50), nullable=True),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("broker", "number"),
    )
    op.create_table(
        "broker_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("account_number", sa.String(40), nullable=False),
        sa.Column("transaction_id", sa.String(60), nullable=False),
        sa.Column("transaction_type", sa.String(40), nullable=False),
        sa.Column("executed_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("broker", "transaction_id"),
    )
    op.create_index(
        "ix_broker_transactions_account_number", "broker_transactions", ["account_number"]
    )
    op.create_index("ix_broker_transactions_executed_at", "broker_transactions", ["executed_at"])
    op.create_table(
        "sync_state",
        sa.Column("broker", sa.String(20), primary_key=True),
        sa.Column("account_number", sa.String(40), primary_key=True),
        sa.Column("synced_through", optionsjourney.db.UTCDateTime(), nullable=True),
        sa.Column("last_attempt_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sync_state")
    op.drop_index("ix_broker_transactions_executed_at", "broker_transactions")
    op.drop_index("ix_broker_transactions_account_number", "broker_transactions")
    op.drop_table("broker_transactions")
    op.drop_table("accounts")
