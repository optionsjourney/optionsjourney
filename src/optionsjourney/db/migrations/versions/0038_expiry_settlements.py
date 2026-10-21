"""Options the app settled at the close on expiration day (decision 0062)

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

import optionsjourney.db

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expiry_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("account_number", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(60), nullable=False),
        sa.Column("underlying", sa.String(20), nullable=False),
        sa.Column("put_call", sa.String(4), nullable=False),
        sa.Column("strike", optionsjourney.db.Money(), nullable=False),
        sa.Column("expiration", sa.Date(), nullable=False),
        sa.Column("sign", sa.Integer(), nullable=False),
        sa.Column("quantity", optionsjourney.db.Money(), nullable=False),
        sa.Column("close_price", optionsjourney.db.Money(), nullable=False),
        sa.Column("fee", optionsjourney.db.Money(), nullable=False),
        sa.Column("created_at", optionsjourney.db.UTCDateTime(), nullable=False),
        sa.UniqueConstraint("broker", "account_number", "symbol", "sign"),
    )
    op.create_index("ix_expiry_settlements_expiration", "expiry_settlements", ["expiration"])


def downgrade() -> None:
    op.drop_index("ix_expiry_settlements_expiration", table_name="expiry_settlements")
    op.drop_table("expiry_settlements")
