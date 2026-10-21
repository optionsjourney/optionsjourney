"""Manual bots: named exit rules attached to any open trade (#158)

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_bots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("target_value", optionsjourney.db.Money(), nullable=True),
        sa.Column("target_unit", sa.String(length=3), nullable=False),
        sa.Column("stop_value", optionsjourney.db.Money(), nullable=True),
        sa.Column("stop_unit", sa.String(length=3), nullable=False),
        sa.Column("close_time", sa.String(length=5), nullable=True),
        sa.Column("close_dte", sa.Integer(), nullable=False),
        sa.Column("alerts", sa.JSON(), nullable=False),
        sa.Column("deleted_at", optionsjourney.db.UTCDateTime(), nullable=True),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    # In place, never a table copy: recreating bots (foreign keys on) would run through
    # the tables that cascade from it. Alembic can't add a foreign key in place on
    # SQLite, but SQLite itself can add a nullable column with REFERENCES. Existing bots
    # are not per-trade and come from no manual bot.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(
            sa.Column("per_trade", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.execute(
        "ALTER TABLE bots ADD COLUMN manual_bot_id INTEGER "
        "REFERENCES manual_bots(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("manual_bot_id")
        batch.drop_column("per_trade")
    op.drop_table("manual_bots")
