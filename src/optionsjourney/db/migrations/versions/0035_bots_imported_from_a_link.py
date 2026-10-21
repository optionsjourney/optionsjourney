"""the link a bot was imported from (decision 0056)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place, as 0016-0034. None: typed, cloned, or imported from a file.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("imported_from_url", sa.String(2000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("imported_from_url")
