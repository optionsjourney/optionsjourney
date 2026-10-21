"""spread alerts: an alert's legs

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_alerts") as batch:
        batch.add_column(sa.Column("legs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_alerts") as batch:
        batch.drop_column("legs")
