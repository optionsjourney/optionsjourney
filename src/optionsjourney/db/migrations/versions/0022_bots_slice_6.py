"""bots slice 6: managing a trade from the journal

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A new table only: existing bots and runs have no overrides, so nothing about
    # how they trade changes. bot_exits gains the kind "close" and the state
    # "reduced" as plain strings (no schema change).
    op.create_table(
        "bot_trade_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("bot_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_position", sa.Integer(), nullable=False),
        sa.Column("stop_pct", optionsjourney.db.Money(), nullable=True),
        sa.Column("target_pct", optionsjourney.db.Money(), nullable=True),
        sa.Column("exits_off", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("changed_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("log", sa.JSON(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("run_id", "group_position"),
    )
    op.create_index("ix_bot_trade_overrides_run_id", "bot_trade_overrides", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_bot_trade_overrides_run_id", table_name="bot_trade_overrides")
    op.drop_table("bot_trade_overrides")
