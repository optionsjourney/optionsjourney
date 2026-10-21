"""Ignore bid-less longs: a close leaves out a bought leg nobody is bidding on (decision 0065)

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0018. Existing bots keep closing every leg.
    with op.batch_alter_table("bot_chase", recreate="never") as batch:
        batch.add_column(
            sa.Column(
                "ignore_bidless_longs", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("bot_chase", recreate="never") as batch:
        batch.drop_column("ignore_bidless_longs")
