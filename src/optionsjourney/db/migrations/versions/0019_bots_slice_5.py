"""bots slice 5: market filters, date lists, economic events, the NYSE calendar

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

MONEY = optionsjourney.db.Money
UTC_DT = optionsjourney.db.UTCDateTime

FLAGS = ("use_vix_level", "use_vix_overnight", "use_vix9d_ratio", "use_technicals",
         "use_intraday_move")  # fmt: skip
AMOUNTS = (
    "vix_min", "vix_max",
    "vix_on_min_up_pct", "vix_on_max_up_pct", "vix_on_min_down_pct", "vix_on_max_down_pct",
    "vix9d_ratio_min", "vix9d_ratio_max",
    "intraday_min_up_pct", "intraday_max_up_pct", "intraday_min_down_pct",
    "intraday_max_down_pct",
)  # fmt: skip
MODES = ("sma_mode", "ema_mode")
PERIODS = ("sma_days_a", "sma_days_b", "ema_minutes_a", "ema_minutes_b")
LISTS = ("specific_date_sets", "blackout_date_sets")


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0018: rebuilding bots would, with foreign
    # keys on, null out order_log.bot_id. Every default is "off" or "no list", so
    # existing bots keep entering exactly as before.
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in FLAGS:
            batch.add_column(
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
            )
        for name in AMOUNTS:
            batch.add_column(sa.Column(name, MONEY(), nullable=True))
        for name in MODES:
            batch.add_column(sa.Column(name, sa.String(10), nullable=True))
        for name in PERIODS:
            batch.add_column(sa.Column(name, sa.Integer(), nullable=True))
        for name in LISTS:
            batch.add_column(sa.Column(name, sa.JSON(), nullable=False, server_default="[]"))
    op.create_table(
        "economic_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_time", sa.String(5), nullable=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("impact", sa.String(10), nullable=False),
        sa.Column("key", sa.String(10), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
    )
    op.create_index("ix_economic_events_event_date", "economic_events", ["event_date"])
    op.create_table(
        "economic_event_sources",
        sa.Column("source", sa.String(30), primary_key=True),
        sa.Column("refreshed_at", UTC_DT(), nullable=True),
        sa.Column("last_attempt_at", UTC_DT(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "market_calendar",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("close_time", sa.String(5), nullable=True),
        sa.Column("name", sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("market_calendar")
    op.drop_table("economic_event_sources")
    op.drop_index("ix_economic_events_event_date", table_name="economic_events")
    op.drop_table("economic_events")
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in reversed(FLAGS + AMOUNTS + MODES + PERIODS + LISTS):
            batch.drop_column(name)
