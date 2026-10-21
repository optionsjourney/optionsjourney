"""Blackout lists checked before expiration (#207, decision 0083)

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0019: rebuilding bots would, with foreign keys on,
    # cascade through its runs. No list is checked before expiration yet, so every
    # existing bot blacks out exactly as before (on the entry day only).
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(
            sa.Column("blackout_dte_sets", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("blackout_dte_sets")
