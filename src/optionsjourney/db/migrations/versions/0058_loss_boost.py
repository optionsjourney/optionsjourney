"""Bots: the loss boost, a bigger next entry after a stopped trade (#328)

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0057. Off for every bot; a blank multiplier reads 2.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("use_loss_boost", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
        batch.add_column(sa.Column("loss_boost_multiplier", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("loss_boost_multiplier")
        batch.drop_column("use_loss_boost")
