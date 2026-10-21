"""bots slice 7: watch rules and profit actions

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None
MONEY = optionsjourney.db.Money

_BOT_NUMBERS = (
    "position_delta_below",
    "position_delta_above",
    "sl_ratio_below",
    "sl_ratio_above",
    "underlying_up_pct",
    "underlying_down_pct",
    "short_put_tested_pts",
    "short_call_tested_pts",
)
_BOT_SWITCHES = ("use_delta_exit", "use_sl_ratio_exit", "use_underlying_exit")


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0022: rebuilding bots would, with foreign
    # keys on, null out order_log.bot_id. Every switch off and every list empty, so
    # existing bots watch nothing. Time action rows gain "close_pct" in their JSON
    # (absent = none); bot_exits.kind gains "watch" and "profit" as plain strings.
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in _BOT_SWITCHES:
            batch.add_column(sa.Column(name, sa.Boolean(), nullable=False,
                                       server_default=sa.false()))  # fmt: skip
        for name in _BOT_NUMBERS:
            batch.add_column(sa.Column(name, MONEY(), nullable=True))
    with op.batch_alter_table("bot_leg_groups", recreate="never") as batch:
        batch.add_column(
            sa.Column("use_profit_actions", sa.Boolean(), nullable=False,
                      server_default=sa.false())
        )  # fmt: skip
        batch.add_column(sa.Column("profit_actions", sa.JSON(), nullable=False,
                                   server_default="[]"))  # fmt: skip
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.add_column(sa.Column("entry_spot", MONEY(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.drop_column("entry_spot")
    with op.batch_alter_table("bot_leg_groups", recreate="never") as batch:
        batch.drop_column("profit_actions")
        batch.drop_column("use_profit_actions")
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in (*_BOT_NUMBERS, *_BOT_SWITCHES):
            batch.drop_column(name)
