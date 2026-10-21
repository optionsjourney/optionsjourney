"""bots slice 17: futures-options bots

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0028. Every existing run is 100 a point, on no
    # futures contract, sets its stops as before (not profit target only) and was chased:
    # the server defaults. bot_runs.status takes "working" (a resting entry) and
    # "cancelled", and a target's bot_exits.state "working" and "pending", as plain values.
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.add_column(sa.Column("multiplier", optionsjourney.db.Money(), nullable=False,
                                   server_default="100"))  # fmt: skip
        batch.add_column(sa.Column("contract", sa.String(20), nullable=True))
        batch.add_column(sa.Column("pt_only", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
        batch.add_column(sa.Column("entry_mode", sa.String(10), nullable=False,
                                   server_default="chase"))  # fmt: skip


def downgrade() -> None:
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        for name in ("entry_mode", "pt_only", "contract", "multiplier"):
            batch.drop_column(name)
