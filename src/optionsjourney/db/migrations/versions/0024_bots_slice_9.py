"""bots slice 9: alerts

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0023. Every existing bot sends its alerts
    # to every channel, has no alert name and no default alerts: as before.
    with op.batch_alter_table("bots", recreate="never") as batch:
        batch.add_column(sa.Column("alert_name", sa.String(40), nullable=True))
        batch.add_column(sa.Column("alert_channel", sa.String(10), nullable=False,
                                   server_default="all"))  # fmt: skip
        batch.add_column(sa.Column("use_default_alerts", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
        batch.add_column(sa.Column("default_alerts", sa.JSON(), nullable=False,
                                   server_default="[]"))  # fmt: skip
        batch.add_column(sa.Column("default_alerts_in_app_only", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
    # A bot's default alerts: the entry's first run and the rule (Tracker2's
    # trade_alerts.bot_run_id / rule_key, unique together); quiet = in the app only.
    with op.batch_alter_table("user_alerts", recreate="never") as batch:
        batch.add_column(sa.Column("bot_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rule_key", sa.String(40), nullable=True))
        batch.add_column(sa.Column("quiet", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))  # fmt: skip
    op.create_index(
        "uq_user_alerts_bot_run_rule", "user_alerts", ["bot_run_id", "rule_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_user_alerts_bot_run_rule", table_name="user_alerts")
    with op.batch_alter_table("user_alerts", recreate="never") as batch:
        for name in ("quiet", "rule_key", "bot_run_id"):
            batch.drop_column(name)
    with op.batch_alter_table("bots", recreate="never") as batch:
        for name in ("default_alerts_in_app_only", "default_alerts", "use_default_alerts",
                     "alert_channel", "alert_name"):  # fmt: skip
            batch.drop_column(name)
