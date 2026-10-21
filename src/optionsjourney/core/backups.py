"""Backup rules (docs/decisions/0023): what a backup file is called, when
one is due, and which old ones go. The copying itself is in
services/backups.py."""

import re
from collections.abc import Iterable
from datetime import datetime, timedelta

from optionsjourney.core.market import MARKET_TZ, market_date

KEEP = 30
_NAME = re.compile(r"^optionsjourney-(\d{4}-\d{2}-\d{2}-\d{4})\.db(\.gz)?$")  # .gz since #13


def backup_name(now: datetime) -> str:
    """e.g. "optionsjourney-2026-10-10-1530.db.gz", in New York time; gzip
    compressed (#13)."""
    return f"optionsjourney-{now.astimezone(MARKET_TZ):%Y-%m-%d-%H%M}.db.gz"


def backup_time(name: str) -> datetime | None:
    """When a backup was made, from its name; None if it isn't one of ours."""
    match = _NAME.match(name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d-%H%M").replace(tzinfo=MARKET_TZ)


def is_due(last: datetime | None, frequency: str, now: datetime) -> bool:
    if frequency == "off":
        return False
    if last is None:
        return True
    if frequency == "weekly":
        return now - last >= timedelta(days=7)
    return market_date(now) > market_date(last)  # daily: once per New York day


def to_delete(names: Iterable[str], keep: int = KEEP) -> list[str]:
    """Our backups beyond the newest `keep`, oldest first. Other files in the
    folder are never touched."""
    ours = sorted((n for n in names if backup_time(n) is not None), key=backup_time)
    return ours[: max(len(ours) - keep, 0)]
