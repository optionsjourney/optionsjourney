"""trades take either side: each group chooses which exit rests at the broker

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Decision 0048: Rests at the broker, per group. A bot that opened for a debit had every
    # group's target watched while nothing could rest: from its next trade its target rests.
    # In place, as 0016-0032.
    with op.batch_alter_table("bot_leg_groups", recreate="never") as batch:
        batch.add_column(
            sa.Column("broker_exit", sa.String(6), nullable=False, server_default="stop")
        )
    op.execute(
        "UPDATE bot_leg_groups SET broker_exit = 'target' "
        "WHERE bot_id IN (SELECT id FROM bots WHERE premium_side = 'debit')"
    )
    # A stop the app holds on its short while the group's target rests at TastyTrade: false on
    # every existing row, so an open trade keeps managing exactly as it started.
    with op.batch_alter_table("bot_exits", recreate="never") as batch:
        batch.add_column(
            sa.Column("app_held", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # What an entry priced at and what each group would set (Preview now and every run).
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.add_column(sa.Column("priced", sa.Text(), nullable=True))
    # Opens for goes: a trade takes the side it prices on (decision 0048)
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("premium_side")


def downgrade() -> None:
    # Opens for comes back: debit for a bot all of whose groups rest their target
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(
            sa.Column("premium_side", sa.String(6), nullable=False, server_default="credit")
        )
    op.execute(
        "UPDATE bots SET premium_side = 'debit' WHERE id IN (SELECT bot_id FROM bot_leg_groups "
        "GROUP BY bot_id HAVING MIN(broker_exit) = 'target')"
    )
    with op.batch_alter_table("bot_runs", recreate="never") as batch:
        batch.drop_column("priced")
    with op.batch_alter_table("bot_exits", recreate="never") as batch:
        batch.drop_column("app_held")
    with op.batch_alter_table("bot_leg_groups", recreate="never") as batch:
        batch.drop_column("broker_exit")
