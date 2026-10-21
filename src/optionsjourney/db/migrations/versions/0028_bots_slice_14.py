"""bots slice 14: legs with their own DTE, trades held overnight

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0027. Every existing bot is 0 DTE, and exact
    # 0 DTE is "today must be listed": what the entry did before. bot_exits.kind takes
    # "settle" (the far legs sold after the nearer legs expired) as a plain value.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("use_exact_dte", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))  # fmt: skip


def downgrade() -> None:
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("use_exact_dte")
