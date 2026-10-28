"""Bots: switches for Min short width and Keep trying (#315)

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0031. On: a number set before the switch still counts,
    # and a blank one is off either way (definition.min_width, keep_trying).
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in ("use_min_short_width", "use_keep_trying"):
            batch.add_column(sa.Column(name, sa.Boolean(), nullable=False,
                                       server_default=sa.true()))  # fmt: skip


def downgrade() -> None:
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("use_keep_trying")
        batch.drop_column("use_min_short_width")
