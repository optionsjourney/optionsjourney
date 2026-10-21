"""When the journal syncs on its own (#171): the Settings page's two choices, one for
market hours and one for other times, each a number of minutes or "off"."""

from datetime import datetime, timedelta

from optionsjourney.core.sessions import session_now


def interval(now: datetime, market_hours: str, other_times: str) -> timedelta | None:
    """How long between syncs right now, or None when syncing is off for this time.
    Market hours are the stock market's regular session (holidays and early closes
    included)."""
    choice = market_hours if session_now("SPY", now) == "regular" else other_times
    if not choice or choice == "off":
        return None
    try:
        minutes = int(choice)
    except ValueError:
        return None
    return timedelta(minutes=minutes) if minutes > 0 else None


def due(last: datetime | None, now: datetime, every: timedelta | None) -> bool:
    """Whether a sync is due: never when off, at once when there's been none yet."""
    if every is None:
        return False
    return last is None or now - last >= every
