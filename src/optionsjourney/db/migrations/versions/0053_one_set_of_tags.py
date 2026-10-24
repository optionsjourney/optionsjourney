"""One set of tags (#278, decision 0110): a bot's labels become journal tags, held with
its main tag (tag_id) first, so every one goes on its trades

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-23
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

RESERVED = {"armed", "off", "live", "paper"}  # definition.RESERVED_TAGS, as they were
TAG_MAX_LEN = 40


def upgrade() -> None:
    conn = op.get_bind()
    tags = {name.lower(): (tag_id, name) for tag_id, name in conn.execute(sa.text(
        "SELECT id, name FROM tags"))}  # fmt: skip
    for bot_id, tag_id, labels in conn.execute(sa.text("SELECT id, tag_id, tags FROM bots")).all():
        main = next((name for tid, name in tags.values() if tid == tag_id), None)
        names: dict[str, str] = {}
        for raw in json.loads(labels) if isinstance(labels, str) else labels or []:
            name = " ".join(str(raw).split())[:TAG_MAX_LEN].strip()
            if name and name.lower() not in RESERVED and name.lower() not in names:
                names[name.lower()] = name
        if main is not None:
            names.pop(main.lower(), None)
        found = []
        for key in sorted(names):
            if key not in tags:
                conn.execute(sa.text("INSERT INTO tags (name) VALUES (:n)"), {"n": names[key]})
                new_id = conn.execute(sa.text("SELECT id FROM tags WHERE name = :n"),
                                      {"n": names[key]}).scalar_one()  # fmt: skip
                tags[key] = (new_id, names[key])
            found.append(tags[key][1])
        held = ([main] if main is not None else []) + found
        conn.execute(sa.text("UPDATE bots SET tags = :t WHERE id = :i"),
                     {"t": json.dumps(held), "i": bot_id})  # fmt: skip


def downgrade() -> None:
    pass  # the labels stay journal tags; the bots' lists still read as labels
