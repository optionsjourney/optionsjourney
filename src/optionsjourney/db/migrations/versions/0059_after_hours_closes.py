"""Bots: after-hours closes on index options, scheduled closes, stops held for the open (#330)

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0057: false on every existing exit
    with op.batch_alter_table("bot_exits", recreate="never") as batch:
        batch.add_column(sa.Column("stop_at_open", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
    op.create_table(
        "bot_scheduled_closes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("group_position", sa.Integer(), nullable=True),
        sa.Column("groups", sa.JSON(), nullable=True),
        sa.Column("quantity", sa.String(length=20), nullable=True),
        sa.Column("order_type", sa.String(length=10), nullable=False),
        sa.Column("start_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["bot_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bot_scheduled_closes_run_id", "bot_scheduled_closes", ["run_id"])
    # One waiting close per run, even when two clicks race past schedule()'s check
    op.create_index(
        "uq_bot_scheduled_closes_one_waiting",
        "bot_scheduled_closes",
        ["run_id"],
        unique=True,
        sqlite_where=sa.text("state = 'scheduled'"),
        postgresql_where=sa.text("state = 'scheduled'"),
    )


def downgrade() -> None:
    op.drop_index("uq_bot_scheduled_closes_one_waiting", table_name="bot_scheduled_closes")
    op.drop_index("ix_bot_scheduled_closes_run_id", table_name="bot_scheduled_closes")
    op.drop_table("bot_scheduled_closes")
    with op.batch_alter_table("bot_exits", recreate="never") as batch:
        batch.drop_column("stop_at_open")
