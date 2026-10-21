"""bots slice 1: sizing, caps, premium bounds and the calendar

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

MONEY = optionsjourney.db.Money

BOT_COLUMNS = (
    "max_entries_per_day", "max_open_trades", "use_premium_bounds", "premium_side",
    "premium_min", "premium_max", "frequency", "entry_days", "monthly_week",
    "monthly_weekday", "specific_dates", "use_blackout_days", "blackout_days",
)  # fmt: skip


def upgrade() -> None:
    # recreate="never": added in place. Rebuilding bots would, with foreign keys
    # on, null out order_log.bot_id. Existing bots get today's behavior: daily,
    # no caps, no bounds, no blackout.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("max_entries_per_day", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("max_open_trades", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("use_premium_bounds", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("premium_side", sa.String(6), nullable=False, server_default="credit")
        )
        batch.add_column(sa.Column("premium_min", MONEY(), nullable=True))
        batch.add_column(sa.Column("premium_max", MONEY(), nullable=True))
        batch.add_column(
            sa.Column("frequency", sa.String(14), nullable=False, server_default="daily")
        )
        batch.add_column(sa.Column("entry_days", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("monthly_week", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("monthly_weekday", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("specific_dates", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column("use_blackout_days", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("blackout_days", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("bot_entry_times", recreate="never") as batch:
        batch.add_column(sa.Column("quantity", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("label", sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_entry_times", recreate="never") as batch:
        batch.drop_column("label")
        batch.drop_column("quantity")
    with op.batch_alter_table("bots", recreate="never") as batch:
        for column in reversed(BOT_COLUMNS):
            batch.drop_column(column)
