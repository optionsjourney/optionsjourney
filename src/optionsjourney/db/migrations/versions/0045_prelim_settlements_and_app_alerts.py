"""Prelim settlements and the app's own alerts (#186)

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-19
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("expiry_settlements") as batch:
        batch.add_column(
            sa.Column("status", sa.String(length=8), nullable=False, server_default="final")
        )
        batch.add_column(
            sa.Column("price_source", sa.String(length=40), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("prelim_close", optionsjourney.db.Money(), nullable=True))
        batch.add_column(sa.Column("finalized_at", optionsjourney.db.UTCDateTime(), nullable=True))
        batch.add_column(
            sa.Column("broker_alerted_at", optionsjourney.db.UTCDateTime(), nullable=True)
        )
    # Settlements from before #186 were replaced by the brokers' records long ago: mark them
    # checked so the first comparison doesn't alert about old trades
    op.execute(
        sa.table(
            "expiry_settlements", sa.column("broker_alerted_at", optionsjourney.db.UTCDateTime())
        )
        .update()
        .values(broker_alerted_at=datetime.now(UTC))
    )
    op.create_table(
        "app_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("link", sa.String(length=400), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("read_at", optionsjourney.db.UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )


def downgrade() -> None:
    op.drop_table("app_alerts")
    with op.batch_alter_table("expiry_settlements") as batch:
        batch.drop_column("broker_alerted_at")
        batch.drop_column("finalized_at")
        batch.drop_column("prelim_close")
        batch.drop_column("price_source")
        batch.drop_column("status")
