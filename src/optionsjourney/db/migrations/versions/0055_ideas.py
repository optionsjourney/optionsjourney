"""Trade Ideas: saved tickets followed live, then frozen at settlement (#292)

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ideas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("cards", sa.JSON(), nullable=False),
        sa.Column("spot_at_entry", optionsjourney.db.Money(), nullable=True),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("final_pnl", optionsjourney.db.Money(), nullable=True),
        sa.Column("final_at", optionsjourney.db.UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,  # a deleted idea's id is never given out again
    )


def downgrade() -> None:
    op.drop_table("ideas")
