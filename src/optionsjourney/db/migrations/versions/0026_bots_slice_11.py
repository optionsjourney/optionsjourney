"""bots slice 11: Schwab bots

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0025: rebuilding bot_runs would, with foreign
    # keys on, null out order_log.bot_run_id. A run trades at the broker it fired at; every
    # run before this slice fired at TastyTrade, so their exits are unchanged.
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.add_column(sa.Column("broker", sa.String(20), nullable=False,
                                   server_default="tastytrade"))  # fmt: skip


def downgrade() -> None:
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.drop_column("broker")
