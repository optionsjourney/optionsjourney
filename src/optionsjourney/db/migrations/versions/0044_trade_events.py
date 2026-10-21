"""Trade events: a journal trade opening, rolling or closing, for the app's popups (#178)

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=400), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("position_key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("bot_name", sa.String(length=200), nullable=True),
        sa.Column("broker", sa.String(length=20), nullable=False),
        sa.Column("account_number", sa.String(length=40), nullable=False),
        sa.Column("amount", optionsjourney.db.Money(), nullable=False),
        sa.Column("occurred_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('open', 'roll', 'partial_close', 'close')", name="ck_trade_events_kind"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_trade_events_position_key", "trade_events", ["position_key"])
    op.create_index("ix_trade_events_occurred_at", "trade_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_trade_events_occurred_at", table_name="trade_events")
    op.drop_index("ix_trade_events_position_key", table_name="trade_events")
    op.drop_table("trade_events")
