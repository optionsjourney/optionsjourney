"""Backup rules (docs/decisions/0023): what a backup file is called, when
one is due, and which old ones go. The copying itself is in
services/backups.py."""

import re
from collections.abc import Iterable
from datetime import datetime, timedelta

from optionsjourney.core.market import MARKET_TZ, market_date

KEEP = 30
_NAME = re.compile(r"^optionsjourney-(\d{4}-\d{2}-\d{2}-\d{4})\.db(\.gz)?$")  # .gz since #13
UPGRADE_KEEP = 3
_UPGRADE = re.compile(
    r"^optionsjourney-(\d{4}-\d{2}-\d{2}-\d{4})"
    r"-before-([0-9A-Za-z.+]+)-from-([0-9A-Za-z.+]+)\.db\.gz$"
)


def backup_name(now: datetime) -> str:
    """e.g. "optionsjourney-2026-10-10-1530.db.gz", in New York time; gzip
    compressed (#13)."""
    return f"optionsjourney-{now.astimezone(MARKET_TZ):%Y-%m-%d-%H%M}.db.gz"


def backup_time(name: str) -> datetime | None:
    """When a backup was made, from its name; None if it isn't one of ours."""
    match = _NAME.match(name) or _UPGRADE.match(name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d-%H%M").replace(tzinfo=MARKET_TZ)


def upgrade_backup_name(now: datetime, from_version: str, to_version: str) -> str:
    """The backup made just before an upgrade, e.g.
    "optionsjourney-2026-10-21-1530-before-0.2.1-from-0.2.0.db.gz"."""
    stamp = now.astimezone(MARKET_TZ)
    return f"optionsjourney-{stamp:%Y-%m-%d-%H%M}-before-{to_version}-from-{from_version}.db.gz"


def upgrade_versions(name: str) -> tuple[str, str] | None:
    """(from, to) versions of a pre-upgrade backup; None for any other name."""
    match = _UPGRADE.match(name)
    return (match.group(3), match.group(2)) if match else None


def is_due(last: datetime | None, frequency: str, now: datetime) -> bool:
    if frequency == "off":
        return False
    if last is None:
        return True
    if frequency == "weekly":
        return now - last >= timedelta(days=7)
    return market_date(now) > market_date(last)  # daily: once per New York day


def to_delete(names: Iterable[str], keep: int = KEEP) -> list[str]:
    """Our usual backups beyond the newest `keep`, oldest first. Other files in the
    folder are never touched."""
    ours = sorted((n for n in names if _NAME.match(n)), key=backup_time)
    return ours[: max(len(ours) - keep, 0)]


def upgrades_to_delete(names: Iterable[str], keep: int = UPGRADE_KEEP) -> list[str]:
    """Pre-upgrade backups beyond the newest `keep`, oldest first. They are
    cleaned up apart from the usual ones, which never delete them."""
    ours = sorted((n for n in names if _UPGRADE.match(n)), key=backup_time)
    return ours[: max(len(ours) - keep, 0)]
