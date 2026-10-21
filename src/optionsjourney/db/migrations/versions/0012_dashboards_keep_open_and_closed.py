"""dashboards saved with no status keep showing open and closed trades

The journal's trade list now opens on open trades (#68). Before, a dashboard
with no status meant open and closed, so say so.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE dashboards SET filters = json_set(filters, '$.status', 'all') "
            "WHERE json_extract(filters, '$.status') IS NULL"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE dashboards SET filters = json_remove(filters, '$.status') "
            "WHERE json_extract(filters, '$.status') = 'all'"
        )
    )
