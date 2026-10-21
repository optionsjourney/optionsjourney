"""bots and every order they tried

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "strategy_id",
            sa.Integer(),
            sa.ForeignKey("strategies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("account_number", sa.String(40), nullable=False),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("max_per_trade", sa.Integer(), nullable=False),
        sa.Column("max_per_day", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    op.create_table(
        "bot_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "bot_id", sa.Integer(), sa.ForeignKey("bots.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", optionsjourney.db.Money(), nullable=False),
        sa.Column("order", sa.JSON(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("broker_order_id", sa.String(60), nullable=True),
    )
    op.create_index("ix_bot_orders_bot_id", "bot_orders", ["bot_id"])
    op.create_index("ix_bot_orders_created_at", "bot_orders", ["created_at"])


def downgrade() -> None:
    op.drop_table("bot_orders")
    op.drop_table("bots")
