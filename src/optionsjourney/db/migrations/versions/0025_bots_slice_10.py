"""bots slice 10: more entry styles

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0024. Every existing bot keeps firing at its
    # entry times (floating off). bot_legs.pick takes "delta" and "spot" as plain values.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("use_floating_entry", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
        batch.add_column(sa.Column("floating_start", sa.String(5), nullable=True))
        batch.add_column(sa.Column("floating_end", sa.String(5), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in ("floating_end", "floating_start", "use_floating_entry"):
            batch.drop_column(name)
