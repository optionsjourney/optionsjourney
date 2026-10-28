"""Bots: an entry time keeps trying, then a fallback premium (#314)

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0031: rebuilding bots would, with foreign keys on,
    # null out order_log.bot_id. Null is what a bot did before: one try, no fallback.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("keep_trying_minutes", sa.Integer(), nullable=True))
    with op.batch_alter_table("bot_legs", recreate="never") as batch:
        batch.add_column(sa.Column("fallback_target", optionsjourney.db.Money(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_legs", recreate="never") as batch:
        batch.drop_column("fallback_target")
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("keep_trying_minutes")
