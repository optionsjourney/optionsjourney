"""A closed trade's strikes and rolls on its trade event (#212)

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trade_events") as batch:
        batch.add_column(sa.Column("detail", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("trade_events") as batch:
        batch.drop_column("detail")
