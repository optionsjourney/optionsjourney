"""bots slice 21: per-trade settings and closing another group after a stop

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None
MONEY = optionsjourney.db.Money
_OVERRIDE = (
    ("stop_unit", sa.String(3)),
    ("stop_usd", MONEY()),
    ("co_mode", sa.String(10)),
    ("co_amount", MONEY()),
    ("on_stop_remainder", sa.String(20)),
    ("remainder_limit", MONEY()),
    ("use_profit_actions", sa.Boolean()),
    ("profit_actions", sa.JSON()),
)


def upgrade() -> None:
    # A group's own copy of its stop style, wing rule and profit actions on one trade
    # (Tracker2's BotPositionOverride columns): null is the group's own, so every existing
    # trade reads what it read before. In place, as 0016-0031.
    with op.batch_alter_table("bot_trade_overrides", recreate="never") as batch:
        for name, kind in _OVERRIDE:
            batch.add_column(sa.Column(name, kind, nullable=True))
    # When a group's stop fills, also close this group (its position; null: none). Tracker2's
    # on_stop_close_group_id; no existing group has one.
    with op.batch_alter_table("bot_leg_groups", recreate="never") as batch:
        batch.add_column(sa.Column("on_stop_close_group", sa.Integer(), nullable=True))
    # A trade's own timed close and watch rules (Tracker2's bot_run_timed_close_overrides and
    # bot_run_watch_overrides): a row replaces the bot's setting for that run; no row is the
    # bot's. Their own tables, so one never wipes the other.
    op.create_table(
        "bot_run_timed_closes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("bot_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("close_time", sa.String(5), nullable=True),
        sa.Column("close_dte", sa.Integer(), nullable=True),
        sa.Column("changed_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("run_id"),
    )
    op.create_table(
        "bot_run_watch_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("bot_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("use_delta_exit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position_delta_below", MONEY(), nullable=True),
        sa.Column("position_delta_above", MONEY(), nullable=True),
        sa.Column("use_sl_ratio_exit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sl_ratio_below", MONEY(), nullable=True),
        sa.Column("sl_ratio_above", MONEY(), nullable=True),
        sa.Column("leg_deltas", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("changed_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("bot_run_watch_rules")
    op.drop_table("bot_run_timed_closes")
    with op.batch_alter_table("bot_leg_groups", recreate="never") as batch:
        batch.drop_column("on_stop_close_group")
    with op.batch_alter_table("bot_trade_overrides", recreate="never") as batch:
        for name, _ in reversed(_OVERRIDE):
            batch.drop_column(name)
