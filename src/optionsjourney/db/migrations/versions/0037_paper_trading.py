"""Paper trading: a run's Paper marker and its pretend fills (decision 0061)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every run so far traded for real or previewed, so none is Paper. In place, as 0016-0033.
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.add_column(
            sa.Column("paper", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("bot_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("group", sa.Integer(), nullable=True),
        sa.Column("price", optionsjourney.db.Money(), nullable=False),
        sa.Column("contracts", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(60), nullable=False),
        sa.Column("legs", sa.JSON(), nullable=False),
        sa.Column("at", optionsjourney.db.UTCDateTime(), nullable=False),
    )
    op.create_index("ix_paper_fills_run_id", "paper_fills", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_fills_run_id", table_name="paper_fills")
    op.drop_table("paper_fills")
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.drop_column("paper")
