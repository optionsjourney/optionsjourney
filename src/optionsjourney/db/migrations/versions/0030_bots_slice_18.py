"""bots slice 18: the stop's and the target's own switches

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Slice 6's single "exits off" becomes Tracker2's two switches (stop_loss_off,
    # profit_target_off). A trade that had exits off has both off, so it keeps its meaning;
    # every other trade has both on (the server defaults). In place, as 0016-0029.
    with op.batch_alter_table("bot_trade_overrides", recreate="never") as batch:
        batch.add_column(sa.Column("stop_off", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
        batch.add_column(sa.Column("target_off", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
    op.execute("UPDATE bot_trade_overrides SET stop_off = exits_off, target_off = exits_off")
    with op.batch_alter_table("bot_trade_overrides", recreate="never") as batch:
        batch.drop_column("exits_off")


def downgrade() -> None:
    # Back to the single switch: on only where both were off (a stop switched off alone
    # then reads as a side with no working stop, which the integrity check reports).
    with op.batch_alter_table("bot_trade_overrides", recreate="never") as batch:
        batch.add_column(sa.Column("exits_off", sa.Boolean(), nullable=False,
                                   server_default="0"))  # fmt: skip
    op.execute(
        "UPDATE bot_trade_overrides SET exits_off = CASE WHEN stop_off AND target_off "
        "THEN 1 ELSE 0 END"
    )
    with op.batch_alter_table("bot_trade_overrides", recreate="never") as batch:
        batch.drop_column("target_off")
        batch.drop_column("stop_off")
