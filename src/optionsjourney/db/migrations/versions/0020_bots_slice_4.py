"""bots slice 4: the timed close and time actions

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0019: rebuilding bots would, with foreign
    # keys on, null out order_log.bot_id. No close time and no time actions: existing
    # bots' exits are exactly as before. bot_exits.kind gains "timed" and "action" and
    # its state "missed" as plain strings, with no schema change.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("close_time", sa.String(5), nullable=True))
        batch.add_column(sa.Column("close_dte", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(
            sa.Column("use_time_actions", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("time_actions", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in ("time_actions", "use_time_actions", "close_dte", "close_time"):
            batch.drop_column(name)
