"""the order log for every order, tick sizes, and Freeze replacing Stop all bots

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("account_number", sa.String(40), nullable=False),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column(
            "bot_id", sa.Integer(), sa.ForeignKey("bots.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("opening", sa.Boolean(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", optionsjourney.db.Money(), nullable=True),
        sa.Column("order_type", sa.String(12), nullable=False),
        sa.Column("order", sa.JSON(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("broker_order_id", sa.String(60), nullable=True),
        sa.Column("replaces_id", sa.String(60), nullable=True),
        sa.Column("tag", sa.String(40), nullable=True),
        sa.Column("cleared_at", optionsjourney.db.UTCDateTime(), nullable=True),
    )
    op.create_index("ix_order_log_created_at", "order_log", ["created_at"])
    op.create_index("ix_order_log_bot_id", "order_log", ["bot_id"])
    op.execute(
        "INSERT INTO order_log (id, created_at, broker, account_number, origin, bot_id, action, "
        'opening, quantity, price, order_type, "order", result, ok, broker_order_id) '
        "SELECT o.id, o.created_at, b.broker, b.account_number, 'bot', o.bot_id, o.action, 1, "
        "o.quantity, o.price, 'limit', o.\"order\", o.result, o.ok, o.broker_order_id "
        "FROM bot_orders o JOIN bots b ON b.id = o.bot_id"
    )
    op.drop_table("bot_orders")
    op.create_table(
        "tick_sizes",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("sizes", sa.JSON(), nullable=False),
        sa.Column("fetched_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    op.execute("UPDATE settings SET key = '_freeze' WHERE key = '_bots_stopped'")


def downgrade() -> None:
    op.execute("UPDATE settings SET key = '_bots_stopped' WHERE key = '_freeze'")
    op.drop_table("tick_sizes")
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
    op.execute(
        'INSERT INTO bot_orders (id, bot_id, created_at, action, quantity, price, "order", '
        "result, ok, broker_order_id) SELECT id, bot_id, created_at, action, quantity, "
        'COALESCE(price, 0), "order", result, ok, broker_order_id FROM order_log '
        "WHERE bot_id IS NOT NULL"
    )
    op.drop_table("order_log")
