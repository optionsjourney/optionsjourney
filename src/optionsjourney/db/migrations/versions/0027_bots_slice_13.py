"""bots slice 13: any same-day option structure

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-13
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # In place (recreate="never"), as 0016-0026: rebuilding bot_exits would, with foreign
    # keys on, null out references to it. short_symbol/long_symbol stay NOT NULL.
    with op.batch_alter_table("bot_exits", recreate="never") as batch:
        batch.add_column(sa.Column("legs", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("anchor_symbol", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column("watched", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # Every bot before this slice was ratio 1 on a short + long pair. A wing row closes
    # only the long; every other kind (stop, target, timed, action, close, watch, profit)
    # acts on the pair. The anchor is the short either way.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, kind, short_symbol, long_symbol FROM bot_exits"))
    for row_id, kind, short, long in rows.fetchall():
        legs = [{"symbol": long, "side": "buy", "ratio": 1}]
        if kind != "wing":
            legs.insert(0, {"symbol": short, "side": "sell", "ratio": 1})
        bind.execute(
            sa.text("UPDATE bot_exits SET legs = :legs, anchor_symbol = :anchor WHERE id = :id"),
            {"legs": json.dumps(legs), "anchor": short, "id": row_id},
        )
    # Before this slice the entry refused any debit, so every existing bot opened for a
    # credit whatever its stored premium side (the select was saved even with premium
    # bounds off). Now the side decides what a bot opens for: every bot is a credit bot.
    bind.execute(sa.text("UPDATE bots SET premium_side = 'credit'"))


def downgrade() -> None:
    with op.batch_alter_table("bot_exits", recreate="never") as batch:
        batch.drop_column("watched")
        batch.drop_column("anchor_symbol")
        batch.drop_column("legs")
