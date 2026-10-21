"""ForexFactory's forecast and previous figures on each economic event (#221)

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("economic_events") as batch:
        batch.add_column(sa.Column("forecast", sa.String(20), nullable=True))
        batch.add_column(sa.Column("previous", sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("economic_events") as batch:
        batch.drop_column("previous")
        batch.drop_column("forecast")
