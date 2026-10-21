"""Manual bots: the Trade ticket's pending attachment until its order fills (#158)

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_manual",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broker", sa.String(length=20), nullable=False),
        sa.Column("account_number", sa.String(length=40), nullable=False),
        sa.Column("order_id", sa.String(length=60), nullable=False),
        sa.Column("manual_bot_id", sa.Integer(), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("attached_contracts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("read_misses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done_at", optionsjourney.db.UTCDateTime(), nullable=True),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )


def downgrade() -> None:
    op.drop_table("pending_manual")
