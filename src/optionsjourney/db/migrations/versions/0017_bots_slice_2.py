"""bots slice 2: stop styles, units and the wing after a stop

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

MONEY = optionsjourney.db.Money
GROUP_COLUMNS = (
    "stop_basis", "stop_unit", "stop_usd", "target_unit", "target_usd",
    "on_stop_remainder", "on_stop_remainder_limit",
)  # fmt: skip


def upgrade() -> None:
    # In place (recreate="never"), as 0016: rebuilding bots would, with foreign
    # keys on, null out order_log.bot_id. Existing bots keep their stops: % above
    # the short's fill, no closing order, the wing sold after a stop.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(
            sa.Column("stop_co_mode", sa.String(10), nullable=False, server_default="off")
        )
        batch.add_column(sa.Column("stop_co_amount", MONEY(), nullable=True))
    with op.batch_alter_table("bot_leg_groups", recreate="never") as batch:
        batch.add_column(
            sa.Column("stop_basis", sa.String(20), nullable=False, server_default="leg_premium")
        )
        batch.add_column(sa.Column("stop_unit", sa.String(3), nullable=False, server_default="pct"))
        batch.add_column(sa.Column("stop_usd", MONEY(), nullable=True))
        batch.add_column(
            sa.Column("target_unit", sa.String(3), nullable=False, server_default="pct")
        )
        batch.add_column(sa.Column("target_usd", MONEY(), nullable=True))
        batch.add_column(
            sa.Column(
                "on_stop_remainder", sa.String(20), nullable=False, server_default="close_market"
            )
        )
        batch.add_column(sa.Column("on_stop_remainder_limit", MONEY(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_leg_groups", recreate="never") as batch:
        for column in reversed(GROUP_COLUMNS):
            batch.drop_column(column)
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("stop_co_amount")
        batch.drop_column("stop_co_mode")
