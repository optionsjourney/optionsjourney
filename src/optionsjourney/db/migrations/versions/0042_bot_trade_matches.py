"""Match trades: a past trade's link to the bot it was matched to (#169)

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_trade_matches",
        sa.Column("position_key", sa.String(length=160), nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("matched_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("position_key"),
    )
    op.create_index("ix_bot_trade_matches_bot_id", "bot_trade_matches", ["bot_id"])


def downgrade() -> None:
    op.drop_index("ix_bot_trade_matches_bot_id", table_name="bot_trade_matches")
    op.drop_table("bot_trade_matches")
