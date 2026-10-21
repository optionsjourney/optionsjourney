"""chart grid: layouts, tabs, drawings and watchlist groups

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chart_layouts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    op.create_table(
        "chart_tabs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "layout_id",
            sa.String(36),
            sa.ForeignKey("chart_layouts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    op.create_table(
        "chart_drawings",
        sa.Column("symbol", sa.String(20), primary_key=True),
        sa.Column("interval", sa.String(4), primary_key=True),
        sa.Column("drawings", sa.JSON(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    op.create_table(
        "watchlist_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("collapsed", sa.Boolean(), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", optionsjourney.db.UTCDateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("watchlist_groups")
    op.drop_table("chart_drawings")
    op.drop_table("chart_tabs")
    op.drop_table("chart_layouts")
