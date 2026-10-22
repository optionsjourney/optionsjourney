"""Release tags and the market-open window, for the update check in the menu
bar app. Pure rules only; asking GitHub and installing are done elsewhere."""

import re
from datetime import datetime, time

from optionsjourney.core.market import MARKET_TZ

_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_OPEN = time(9, 30)
_CLOSE = time(16, 0)


def parse_tag(tag: str) -> tuple[int, int, int] | None:
    """The tag "v0.1.10" or "0.1.10" as (0, 1, 10); None if it isn't a plain
    three-number version (so betas and odd tags are ignored)."""
    match = _TAG.match(tag)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def is_newer(tag: str, running: str) -> bool:
    """True if the release tag is a higher version than the running one.
    Either side not being a plain version (e.g. "0+unknown", a copy that was
    never installed) means no offer."""
    new, current = parse_tag(tag), parse_tag(running)
    return new is not None and current is not None and new > current


def market_open(now: datetime) -> bool:
    """Weekdays 9:30 to 16:00 New York time. Holidays aren't checked; a
    warning on a holiday is harmless."""
    local = now.astimezone(MARKET_TZ)
    return local.weekday() < 5 and _OPEN <= local.time() < _CLOSE
