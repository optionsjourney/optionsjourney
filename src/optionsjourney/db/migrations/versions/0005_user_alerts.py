"""user alerts: prices and trades' open P/L going above or below a level

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target", sa.String(160), nullable=False),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("level", optionsjourney.db.Money(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("triggered_at", optionsjourney.db.UTCDateTime(), nullable=True),
        sa.Column("triggered_value", optionsjourney.db.Money(), nullable=True),
        sa.Column("dismissed", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_alerts")
