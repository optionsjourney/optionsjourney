"""only a group of one sold leg rests its stop (decision 0054)

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

NOTICE = (
    "This bot was switched off and back to preview: a group of more than one leg can no "
    "longer rest its stop at the broker, so its profit target rests there and the app "
    "watches its stop (decision 0054). The stop works only while this computer is on and "
    "awake and the app is running. Arm it again with the watched box ticked, or give each "
    "sold leg a group of its own to rest its stop."
)
# Each group of more than one leg, by bot and position
MULTI = (
    "SELECT bot_id, group_position FROM bot_legs GROUP BY bot_id, group_position "
    "HAVING COUNT(*) > 1"
)


def upgrade() -> None:
    # In place, as 0016-0033. A notice for the bot's page, alerted once at startup.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("notice", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("notice_sent", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # An armed or live bot whose stops move from the broker to the app is switched off first,
    # so it's armed again knowingly (as saving such a change does)
    op.execute(
        sa.text(
            "UPDATE bots SET enabled = 0, live = 0, notice = :notice WHERE (enabled = 1 OR "
            "live = 1) AND id IN (SELECT m.bot_id FROM bot_leg_groups g JOIN (" + MULTI + ") m "
            "ON m.bot_id = g.bot_id AND m.group_position = g.position "
            "WHERE g.broker_exit = 'stop')"
        ).bindparams(notice=NOTICE)
    )
    op.execute(
        "UPDATE bot_leg_groups SET broker_exit = 'target' WHERE EXISTS (SELECT 1 FROM ("
        + MULTI
        + ") m WHERE m.bot_id = bot_leg_groups.bot_id AND m.group_position = "
        "bot_leg_groups.position)"
    )


def downgrade() -> None:
    # The groups keep resting their targets: which ones rested their stops isn't kept
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.drop_column("notice_sent")
        batch.drop_column("notice")
