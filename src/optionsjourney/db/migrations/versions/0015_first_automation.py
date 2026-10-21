"""the first automation: bot definitions, runs and exits

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

UTC = optionsjourney.db.UTCDateTime
MONEY = optionsjourney.db.Money


def _bot_fk() -> sa.ForeignKey:
    return sa.ForeignKey("bots.id", ondelete="CASCADE")


def upgrade() -> None:
    # recreate="never": SQLite adds and drops these columns in place. Rebuilding
    # the bots table would, with foreign keys on, null out order_log.bot_id.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(
            sa.Column("underlying", sa.String(20), nullable=False, server_default="SPX")
        )
        batch.add_column(sa.Column("contracts", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(
            sa.Column("split_iron_condors", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(
            sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("updated_at", UTC(), nullable=True))
        batch.drop_column("config")
    op.create_table(
        "bot_legs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.Integer(), _bot_fk(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("right", sa.String(4), nullable=False),
        sa.Column("ratio", sa.Integer(), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=False),
        sa.Column("pick", sa.String(10), nullable=False),
        sa.Column("target", MONEY(), nullable=True),
        sa.Column("linked_position", sa.Integer(), nullable=True),
        sa.Column("exact", sa.Boolean(), nullable=False),
        sa.Column("group_position", sa.Integer(), nullable=False),
    )
    op.create_index("ix_bot_legs_bot_id", "bot_legs", ["bot_id"])
    op.create_table(
        "bot_leg_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.Integer(), _bot_fk(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("stop_pct", MONEY(), nullable=True),
        sa.Column("target_pct", MONEY(), nullable=True),
    )
    op.create_index("ix_bot_leg_groups_bot_id", "bot_leg_groups", ["bot_id"])
    op.create_table(
        "bot_entry_times",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.Integer(), _bot_fk(), nullable=False),
        sa.Column("time", sa.String(5), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_bot_entry_times_bot_id", "bot_entry_times", ["bot_id"])
    op.create_table(
        "bot_chase",
        sa.Column("bot_id", sa.Integer(), _bot_fk(), primary_key=True),
        sa.Column("phase", sa.String(5), primary_key=True),
        sa.Column("start_offset", MONEY(), nullable=False),
        sa.Column("step", MONEY(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("max_tries", sa.Integer(), nullable=False),
    )
    op.create_table(
        "bot_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.Integer(), _bot_fk(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False, unique=True),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("entry_time", sa.String(5), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("account_number", sa.String(40), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("legs", sa.JSON(), nullable=False),
        sa.Column("target_credit", MONEY(), nullable=True),
        sa.Column("fill_credit", MONEY(), nullable=True),
        sa.Column("filled_legs", sa.JSON(), nullable=False),
        sa.Column("broker_order_id", sa.String(60), nullable=True),
        sa.Column("contracts", sa.Integer(), nullable=False),
        sa.Column(
            "parent_run_id",
            sa.Integer(),
            sa.ForeignKey("bot_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chase_log", sa.JSON(), nullable=False),
        sa.Column("filled_at", UTC(), nullable=True),
        sa.Column("created_at", UTC(), nullable=False),
        sa.Column("updated_at", UTC(), nullable=False),
    )
    op.create_index("ix_bot_runs_bot_id", "bot_runs", ["bot_id"])
    op.create_index("ix_bot_runs_trading_date", "bot_runs", ["trading_date"])
    op.create_index("ix_bot_runs_broker_order_id", "bot_runs", ["broker_order_id"])
    op.create_table(
        "bot_exits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("bot_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("group_position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("short_symbol", sa.String(40), nullable=False),
        sa.Column("long_symbol", sa.String(40), nullable=False),
        sa.Column("contracts", sa.Integer(), nullable=False),
        sa.Column("broker_order_id", sa.String(60), nullable=True),
        sa.Column("trigger", MONEY(), nullable=True),
        sa.Column("limit", MONEY(), nullable=True),
        sa.Column("fill_price", MONEY(), nullable=True),
        sa.Column("rearms", sa.Integer(), nullable=False),
        sa.Column("rearm_day", sa.Date(), nullable=True),
        sa.Column("log", sa.JSON(), nullable=False),
        sa.Column("created_at", UTC(), nullable=False),
        sa.Column("updated_at", UTC(), nullable=False),
    )
    op.create_index("ix_bot_exits_run_id", "bot_exits", ["run_id"])
    with op.batch_alter_table("order_log") as batch:
        batch.add_column(sa.Column("bot_run_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_order_log_bot_run_id", "bot_runs", ["bot_run_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("order_log") as batch:
        batch.drop_constraint("fk_order_log_bot_run_id", type_="foreignkey")
        batch.drop_column("bot_run_id")
    for table in (
        "bot_exits",
        "bot_runs",
        "bot_chase",
        "bot_entry_times",
        "bot_leg_groups",
        "bot_legs",
    ):
        op.drop_table(table)
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("config", sa.JSON(), nullable=False, server_default="{}"))
        for column in ("updated_at", "hidden", "split_iron_condors", "contracts", "underlying"):
            batch.drop_column(column)
