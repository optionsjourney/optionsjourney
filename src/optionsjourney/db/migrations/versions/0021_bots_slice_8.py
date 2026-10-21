"""bots slice 8: bot pages and sharing

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0020: rebuilding bots would, with foreign
    # keys on, null out order_log.bot_id. Existing bots: no tags, not archived, not
    # imported, so nothing about how they trade changes.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("archived_at", optionsjourney.db.UTCDateTime(), nullable=True))
        batch.add_column(sa.Column("imported_at", optionsjourney.db.UTCDateTime(), nullable=True))
    op.create_table(
        "bot_integrity_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("bot_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_key", sa.String(30), nullable=False),
        sa.Column("subject", sa.String(40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("first_seen_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.Column("notified_at", optionsjourney.db.UTCDateTime(), nullable=True),
        sa.Column("cleared_at", optionsjourney.db.UTCDateTime(), nullable=True),
        sa.UniqueConstraint("run_id", "check_key", "subject"),
    )
    op.create_index("ix_bot_integrity_alerts_run_id", "bot_integrity_alerts", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_bot_integrity_alerts_run_id", table_name="bot_integrity_alerts")
    op.drop_table("bot_integrity_alerts")
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in ("imported_at", "archived_at", "tags"):
            batch.drop_column(name)
