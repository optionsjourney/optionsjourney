"""bots slice 3: closing by chase

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016 and 0017. Existing bots keep today's
    # target close: after its last try it gives up and puts the stop back.
    with op.batch_alter_table("bot_chase", recreate="never") as batch:
        batch.add_column(
            sa.Column("exhaust_mode", sa.String(30), nullable=False, server_default="give_up")
        )
        batch.add_column(
            sa.Column("natural_minutes", sa.Integer(), nullable=False, server_default="2")
        )
    with op.batch_alter_table("bot_exits", recreate="never") as batch:
        batch.add_column(
            sa.Column("cancel_requested_at", optionsjourney.db.UTCDateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("bot_exits", recreate="never") as batch:
        batch.drop_column("cancel_requested_at")
    with op.batch_alter_table("bot_chase", recreate="never") as batch:
        batch.drop_column("natural_minutes")
        batch.drop_column("exhaust_mode")
