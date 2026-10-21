"""journal: strategies, tags, trade edits and dashboards

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Until the setup wizard asks about trading style (roadmap Phase 3).
STARTER_STRATEGIES = ["0DTE", "Credit Spreads", "Iron Condors", "Calendars", "Wheel"]


def upgrade() -> None:
    strategies = op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False, unique=True),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False, unique=True),
    )
    op.create_table(
        "trade_edits",
        sa.Column("position_key", sa.String(160), primary_key=True),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column(
            "strategy_id",
            sa.Integer(),
            sa.ForeignKey("strategies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("close_reason", sa.String(40), nullable=True),
        sa.Column("split", sa.Boolean(), nullable=True),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    op.create_table(
        "trade_tags",
        sa.Column("position_key", sa.String(160), primary_key=True),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "dashboards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    now = datetime.now(UTC)
    op.bulk_insert(strategies, [{"name": name, "created_at": now} for name in STARTER_STRATEGIES])


def downgrade() -> None:
    op.drop_table("dashboards")
    op.drop_table("trade_tags")
    op.drop_table("trade_edits")
    op.drop_table("tags")
    op.drop_table("strategies")
