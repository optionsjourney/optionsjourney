"""Schwab bots rest their stops at Schwab (decision 0060)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-16
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Until now every Schwab run's stop on its short was watched by the app because of the
    # run's broker (decision 0037), with nothing at Schwab. From here a stop is the app's
    # only when its row says so (app_held, decision 0048), so each Schwab stop row written
    # before this is marked app-held: an open trade's stop stays watched until it closes,
    # and is never sent to Schwab as a second stop. A group watched on its P&L (watched)
    # is the app's anyway. Data only; nothing else changes.
    op.execute(
        "UPDATE bot_exits SET app_held = 1 WHERE kind = 'stop' AND watched = 0 "
        "AND app_held = 0 AND run_id IN (SELECT id FROM bot_runs WHERE broker = 'schwab')"
    )


def downgrade() -> None:
    # The rows stay app-held: the older code watches every Schwab stop anyway
    pass
