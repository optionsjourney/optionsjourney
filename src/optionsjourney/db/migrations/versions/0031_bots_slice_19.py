"""bots slice 19: entry, sizing and chase settings

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None
MONEY = optionsjourney.db.Money


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0030: rebuilding bots would, with foreign keys
    # on, null out order_log.bot_id. Every default is what a bot did before: sized by
    # contracts, no chase window, no market after the last try, no leg delta thresholds,
    # no min short width, conflicted strikes not moved (a new bot's form starts ticked,
    # Tracker2's default). A profit action's row gains "stop_usd" in its JSON (absent: none).
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("sizing", sa.String(10), nullable=False,
                                   server_default="contracts"))  # fmt: skip
        batch.add_column(sa.Column("max_per_entry_usd", MONEY(), nullable=True))
        batch.add_column(sa.Column("min_short_width", MONEY(), nullable=True))
        batch.add_column(sa.Column("move_conflicted_strikes", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
    with op.batch_alter_table("bot_chase", recreate="never") as batch:
        batch.add_column(sa.Column("window_minutes", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("switch_to_market", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
    with op.batch_alter_table("bot_legs", recreate="never") as batch:
        for name in ("exit_delta_below", "exit_delta_above", "max_offset"):
            batch.add_column(sa.Column(name, MONEY(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_legs", recreate="never") as batch:
        for name in ("max_offset", "exit_delta_above", "exit_delta_below"):
            batch.drop_column(name)
    with op.batch_alter_table("bot_chase", recreate="never") as batch:
        batch.drop_column("switch_to_market")
        batch.drop_column("window_minutes")
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in ("move_conflicted_strikes", "min_short_width", "max_per_entry_usd", "sizing"):
            batch.drop_column(name)
